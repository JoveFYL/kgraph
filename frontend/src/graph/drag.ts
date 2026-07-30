import type { Container } from 'pixi.js'
import type { GNode } from './types'
import type { GraphSimulation } from './layout'
import { DRAG_REHEAT, IDLE_ALPHA } from './config'

/**
 * Drag any node and let the simulation react.
 *
 * The whole mechanism is d3's `fx`/`fy`: setting them pins a node to a coordinate and
 * the simulation stops integrating its velocity, while every *other* node keeps
 * obeying the forces. So "dragging" is really just moving a pin and letting the
 * springs sort out the consequences. We never touch `x`/`y` directly — d3 owns those.
 *
 * On release the pin is removed, always. Keeping it would leave the node frozen where
 * you dropped it while its neighbours carried on moving, which looks broken. Letting
 * go hands the node back to the forces, and its children re-form around it.
 *
 * Listeners go on `window`, not the canvas: if the pointer leaves the canvas
 * mid-drag, we still want the moves and the release.
 */
export function createDrag(
    canvas: HTMLCanvasElement,
    world: Container,
    sim: GraphSimulation,
    signal: AbortSignal,
) {
    let node: GNode | null = null
    // Offset between where you grabbed and the node's centre, so a node caught by its
    // edge doesn't snap its middle to the cursor.
    let grabX = 0, grabY = 0

    /** Screen pixels -> world coordinates, undoing the current pan and zoom. */
    const toWorld = (clientX: number, clientY: number) => {
        const r = canvas.getBoundingClientRect()
        return world.toLocal({ x: clientX - r.left, y: clientY - r.top })
    }

    function begin(n: GNode, clientX: number, clientY: number) {
        const p = toWorld(clientX, clientY)
        node = n
        grabX = (n.x ?? p.x) - p.x
        grabY = (n.y ?? p.y) - p.y
        n.fx = n.x; n.fy = n.y

        // Bump the energy while dragging so the graph reacts briskly to being shoved.
        sim.alphaTarget(DRAG_REHEAT).restart()
        canvas.style.cursor = 'grabbing'
    }

    window.addEventListener('pointermove', e => {
        if (!node) return
        const p = toWorld(e.clientX, e.clientY)
        node.fx = p.x + grabX
        node.fy = p.y + grabY
    }, { signal })

    const end = () => {
        if (!node) return
        node.fx = null; node.fy = null   // hand it back to the forces
        node = null
        sim.alphaTarget(IDLE_ALPHA)      // back to the always-on trickle, not to 0
        canvas.style.cursor = ''
    }
    window.addEventListener('pointerup', end, { signal })
    window.addEventListener('pointercancel', end, { signal })

    return { begin, active: () => node !== null }
}
