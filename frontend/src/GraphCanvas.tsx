import { useEffect, useRef } from 'react'
import { Application } from 'pixi.js'
import { BG, CSV_URL } from './graph/config'
import { buildGraph } from './graph/buildGraph'
import { assignAnchors, createSimulation, seedPositions, type GraphSimulation } from './graph/layout'
import { createScene } from './graph/scene'
import { attachControls } from './graph/controls'
import { createDrag } from './graph/drag'
import { createTooltip } from './graph/tooltip'

/**
 * React renders one empty <div> and then gets out of the way. Pixi owns the canvas
 * inside it and repaints 60x a second.
 *
 * The pipeline, top to bottom:
 *   fetch CSV -> buildGraph -> anchors + seed -> Pixi scene -> simulation -> controls
 */
export default function GraphCanvas() {
    const holder = useRef<HTMLDivElement>(null)

    useEffect(() => {
        // `holder.current` is HTMLDivElement | null, and TS discards narrowing of an
        // object property across an `await`. A const can't be reassigned, so it sticks.
        const el = holder.current
        if (!el) return

        let app: Application | undefined
        let sim: GraphSimulation | undefined
        let cancelled = false
        const ac = new AbortController()   // one abort() unhooks every listener
        const tip = createTooltip(el)

        void (async () => {
            const res = await fetch(CSV_URL, { signal: ac.signal })
            if (!res.ok) throw new Error(`${CSV_URL} -> HTTP ${res.status}`)
            const graph = buildGraph(await res.text())
            if (cancelled) return

            const anchors = assignAnchors(graph.nodes, innerWidth, innerHeight)
            seedPositions(graph.nodes, anchors)

            // scene and drag need each other: scene must know where to send a press,
            // and drag needs scene.world to convert screen pixels into graph
            // coordinates. This box breaks the cycle — the handler reads it when a
            // press actually happens, long after it has been filled in below.
            const drag: { current?: ReturnType<typeof createDrag> } = {}

            const scene = createScene(graph, {
                onEnter: (node, x, y) => tip.show(node.label, node.sub, x, y),
                onMove: tip.move,
                onLeave: tip.hide,
                onPress: (node, x, y) => drag.current?.begin(node, x, y),
            })

            sim = createSimulation(graph, anchors)
            sim.on('tick', scene.syncPositions)

            const a = new Application()
            await a.init({
                background: BG,
                resizeTo: window,               // fills the viewport, re-fits on resize
                resolution: devicePixelRatio,   // render at the screen's true density
                autoDensity: true,              // ...but keep the CSS size correct
                antialias: true,                // OFF by default in Pixi; smooths circle rims
            })
            // StrictMode mounts, unmounts and remounts in dev, so the first init can
            // still be in flight when the component is already gone.
            if (cancelled) { a.destroy(true); return }
            app = a

            a.stage.addChild(scene.world)
            el.appendChild(a.canvas)

            // Hover fades run off Pixi's render loop, not the simulation's, so they
            // stay smooth and frame-rate-paced no matter what the physics is doing.
            a.ticker.add(t => scene.update(t.deltaMS))

            drag.current = createDrag(a.canvas, scene.world, sim, ac.signal)

            attachControls(a, scene.world, {
                signal: ac.signal,
                onZoom: scene.setZoom,
                onPanStart: () => { tip.hide(); scene.clearHover() },
                canPan: () => !drag.current?.active(),
            })
        })().catch((err: unknown) => {
            // An aborted fetch is the expected result of unmounting mid-load, not a bug.
            if (err instanceof DOMException && err.name === 'AbortError') return
            console.error('[GraphCanvas]', err)
        })

        return () => {
            cancelled = true
            ac.abort()
            sim?.stop()
            tip.destroy()
            app?.destroy(true, { children: true })
        }
    }, [])

    return <div id="graph" ref={holder} />
}
