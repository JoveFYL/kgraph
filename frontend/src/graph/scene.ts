import { Circle, Container, Graphics, GraphicsContext, Text, type FederatedPointerEvent } from 'pixi.js'
import type { Graph, GNode } from './types'
import { BG, COLOR, DIM, EDGE, EDGE_DIM, FADE_MS, GEO, HIT_PAD, RING, TASK_R } from './config'

export interface NodeHandlers {
    onEnter(node: GNode, screenX: number, screenY: number): void
    onMove(screenX: number, screenY: number): void
    onLeave(): void
    /** Pointer pressed on a node. Coordinates are viewport-relative (clientX/Y). */
    onPress(node: GNode, clientX: number, clientY: number): void
}

/**
 * Everything Pixi. The scene owns one `world` container that the caller pans and
 * zooms; it knows nothing about React, the DOM, or where positions come from.
 *
 * The division of labour that matters: d3-force writes `.x`/`.y` onto the node
 * objects, and `syncPositions()` copies them onto the circles. That copy is the
 * entire handoff between the two libraries.
 */
export function createScene(graph: Graph, handlers: NodeHandlers) {
    const { nodes, links, neighbours } = graph
    const byId = new Map(nodes.map(n => [n.id, n]))

    const world = new Container()
    const edgeLayer = new Graphics()     // all 1,654 links, redrawn every tick
    const hotLayer = new Graphics()      // just the hovered node's links
    const nodeLayer = new Container()
    const labelLayer = new Container()
    // Order matters. BOTH edge layers go under the nodes: a line drawn on top of a
    // circle reads as the circle being transparent, which is not what Obsidian does.
    // Each node's background-coloured ring then cleanly hides the line ends beneath it.
    world.addChild(edgeLayer, hotLayer, nodeLayer, labelLayer)

    let zoom = 1
    let hot: GNode | null = null   // hovered node; retained while the fade-out runs
    let hovering = false           // is the pointer on a node right now
    let settled = true             // every alpha has reached its target

    createCircles()
    const labels = createLabels()

    // --- circles ------------------------------------------------------------------

    function createCircles() {
        // The 1,446 task circles are all the same size and one of only three colours,
        // so they share three GraphicsContexts. A context holds the tessellated
        // geometry, so sharing means Pixi builds 3 circles instead of 1,446 and can
        // batch them. The 212 hubs all differ in radius, so they keep their own.
        const shared = new Map<number, GraphicsContext>()
        for (const c of Object.values(COLOR)) {
            shared.set(c, ringed(new GraphicsContext(), c, TASK_R))
        }

        for (const n of nodes) {
            const k = n.radius / GEO
            const g = n.tier === 'task'
                ? new Graphics(shared.get(n.color)!)
                : ringed(new Graphics(), n.color, n.radius)
            g.scale.set(k)

            g.eventMode = 'static'   // opt this circle into hit-testing
            g.cursor = 'pointer'
            // hitArea is in the Graphics' own pre-scale space, hence the /k. A 3.5px
            // dot is fiddly to hit, so the target is padded by HIT_PAD screen px.
            g.hitArea = new Circle(0, 0, GEO + HIT_PAD / k)

            g.on('pointerover', (e: FederatedPointerEvent) => {
                handlers.onEnter(n, e.global.x, e.global.y)
                setHot(n)
            })
            g.on('pointermove', (e: FederatedPointerEvent) => handlers.onMove(e.global.x, e.global.y))
            g.on('pointerout', () => {
                handlers.onLeave()
                setHot(null)
            })
            // Pixi registers its own canvas listeners at init, before attachControls
            // adds ours — so this fires before the pan handler sees the same press.
            g.on('pointerdown', (e: FederatedPointerEvent) => {
                handlers.onPress(n, e.clientX, e.clientY)
            })

            n.gfx = g                // the link back: node <-> its circle
            nodeLayer.addChild(g)
        }
    }

    /**
     * Draw the circle at GEO (a big radius) so Pixi tessellates it with plenty of
     * segments, then the caller shrinks it. A 3.5px circle drawn at 3.5px becomes a
     * visible polygon when zoomed in; this stays smooth to 12x.
     *
     * The ring is drawn in the background colour OUTSIDE the fill (alignment: 1). It
     * hides the edge lines that would otherwise run under the node and stops touching
     * nodes merging into one blob. Its width is pre-divided by the shrink factor so
     * it lands at RING px on screen.
     */
    function ringed<T extends Graphics | GraphicsContext>(ctx: T, color: number, radius: number): T {
        ctx.circle(0, 0, GEO)
            .fill(color)
            .stroke({ width: RING / (radius / GEO), color: BG, alignment: 1 })
        return ctx
    }

    // --- labels -------------------------------------------------------------------

    /** One label per agency. Text is expensive to rasterise, so four is the budget. */
    function createLabels() {
        const out = new Map<string, Text>()
        for (const n of nodes) {
            if (n.tier !== 'agency') continue
            const t = new Text({
                text: n.label,
                style: {
                    fill: 0xf3f4f6, fontSize: 15, fontWeight: '600',
                    fontFamily: 'system-ui, sans-serif',
                    align: 'center', wordWrap: true, wordWrapWidth: 190,
                },
            })
            t.anchor.set(0.5, 1)
            t.resolution = 2         // stay crisp when zoomed in
            out.set(n.id, t)
            labelLayer.addChild(t)
        }
        return out
    }

    // --- hover highlight ------------------------------------------------------------

    /**
     * Fade everything that isn't the hovered node or one of its neighbours. At 1,658
     * nodes this is the difference between readable and noise — the eye otherwise
     * can't tell which rosette a given task belongs to.
     *
     * The 1,654 background lines are the real noise, so the whole edge layer fades
     * harder than the nodes do. Dimming nodes but leaving every line at full strength
     * is what made this look harsh.
     *
     * This only records the new *target*. `update()` walks the alphas there over
     * FADE_MS, so nothing snaps.
     */
    function setHot(n: GNode | null) {
        // On leave, keep the `hot` reference: the highlight has to stay drawn while
        // it fades out. update() drops it once the fade finishes.
        if (n) hot = n
        hovering = n !== null
        settled = false
        if (n) drawHotEdges()
    }

    /** Where a given node/label should end up, given the current hover state. */
    function targetAlpha(id: string) {
        if (!hovering || !hot) return 1
        if (id === hot.id) return 1
        return neighbours.get(hot.id)?.has(id) ? 1 : DIM
    }

    /**
     * Step every alpha toward its target. Driven by Pixi's ticker, NOT by the
     * simulation tick — `sim.on('tick')` stops firing once the layout settles, so a
     * fade hung off that would freeze half-done on a static graph.
     *
     * Costs one pass over 1,658 objects per frame, and only while something is
     * actually moving: `settled` short-circuits it the rest of the time.
     */
    function update(deltaMS: number) {
        if (settled) return
        const step = deltaMS / FADE_MS
        let done = true

        for (const n of nodes) done = approach(n.gfx!, targetAlpha(n.id), step) && done
        for (const [id, t] of labels) done = approach(t, targetAlpha(id), step) && done
        done = approach(edgeLayer, hovering ? EDGE_DIM : 1, step) && done
        done = approach(hotLayer, hovering ? 1 : 0, step) && done

        if (!done) return
        settled = true
        // Drop the highlight geometry only now it has finished fading out —
        // clearing on pointerout would make the lines vanish instead of dissolve.
        if (!hovering) { hot = null; hotLayer.clear() }
    }

    /** Move one object's alpha toward `to`. Returns true once it has arrived. */
    function approach(obj: { alpha: number }, to: number, step: number) {
        const gap = to - obj.alpha
        if (Math.abs(gap) <= step) { obj.alpha = to; return true }
        obj.alpha += Math.sign(gap) * step
        return false
    }

    function drawHotEdges() {
        hotLayer.clear()
        if (!hot) return
        for (const l of links) {
            const s = l.source as GNode, t = l.target as GNode
            if (s.id !== hot.id && t.id !== hot.id) continue
            hotLayer.moveTo(s.x!, s.y!).lineTo(t.x!, t.y!)
        }
        hotLayer.stroke({ ...EDGE.hot, width: EDGE.hot.width / zoom })
    }

    // --- per-frame ------------------------------------------------------------------

    /**
     * Two passes, because a Graphics stroke() applies to every subpath drawn since the
     * last one: all the thin task edges go down and get stroked, then the thicker job
     * edges. Widths are divided by the zoom so lines stay ~1px on screen instead of
     * fattening into ribbons.
     */
    function drawEdges() {
        edgeLayer.clear()
        for (const tier of ['job', 'agency'] as const) {
            for (const l of links) {
                if ((l.target as GNode).tier !== tier) continue
                const s = l.source as GNode, t = l.target as GNode
                edgeLayer.moveTo(s.x!, s.y!).lineTo(t.x!, t.y!)   // pen: from -> to
            }
            const style = tier === 'job' ? EDGE.task : EDGE.job
            edgeLayer.stroke({ ...style, width: style.width / zoom })
        }
    }

    /** Called every simulation tick: copy d3's coordinates onto the Pixi objects. */
    function syncPositions() {
        drawEdges()
        for (const n of nodes) { n.gfx!.x = n.x!; n.gfx!.y = n.y! }
        for (const [id, t] of labels) {
            const n = byId.get(id)!
            t.x = n.x!; t.y = n.y! - n.radius - 10
        }
        if (hot) drawHotEdges()
    }

    /** Restyle for a new zoom level. Labels are counter-scaled so they stay legible. */
    function setZoom(next: number) {
        zoom = next
        for (const t of labels.values()) t.scale.set(1 / next)
        drawEdges()
        drawHotEdges()
    }

    return { world, syncPositions, setZoom, update, clearHover: () => setHot(null) }
}

export type Scene = ReturnType<typeof createScene>
