import type { Application, Container } from 'pixi.js'
import { MAX_ZOOM, MIN_ZOOM } from './config'

interface Options {
    /** Every listener registers with this, so one abort() unhooks all of them. */
    signal: AbortSignal
    /** Fired after a zoom, so the scene can restyle strokes at the new scale. */
    onZoom(scale: number): void
    /** Fired when a pan starts — a tooltip stuck mid-drag looks broken. */
    onPanStart(): void
    /** False while a node is being dragged, so the canvas doesn't pan underneath it. */
    canPan(): boolean
}

/** Wheel-to-zoom, drag-to-pan, and staying sharp when the display density changes. */
export function attachControls(app: Application, world: Container, opts: Options) {
    const { signal, onZoom, onPanStart, canPan } = opts
    const canvas = app.canvas
    app.renderer.events.cursorStyles.default = 'grab'

    // --- zoom ---------------------------------------------------------------------
    canvas.addEventListener('wheel', e => {
        e.preventDefault()          // stop the page itself from scrolling
        const rect = canvas.getBoundingClientRect()
        const px = e.clientX - rect.left, py = e.clientY - rect.top

        // exp() makes each wheel notch a constant *ratio*, so zooming feels the same
        // whether you're at 0.5x or 8x. Linear steps do not.
        const next = clamp(world.scale.x * Math.exp(-e.deltaY * 0.0015))
        const k = next / world.scale.x

        // Zoom about the cursor: keep whatever world point is under the pointer
        // pinned to that same screen pixel.
        world.x = px - (px - world.x) * k
        world.y = py - (py - world.y) * k
        world.scale.set(next)
        onZoom(next)
    }, { passive: false, signal })

    // --- pan ----------------------------------------------------------------------
    let panning = false, lastX = 0, lastY = 0
    canvas.addEventListener('pointerdown', e => {
        // A press that landed on a node belongs to the drag, not the pan. Checked
        // again on move, so ordering between Pixi's listeners and ours can't matter.
        if (!canPan()) return
        panning = true; lastX = e.clientX; lastY = e.clientY
        onPanStart()
        canvas.setPointerCapture(e.pointerId)
    }, { signal })
    canvas.addEventListener('pointermove', e => {
        if (!panning || !canPan()) return
        world.x += e.clientX - lastX
        world.y += e.clientY - lastY
        lastX = e.clientX; lastY = e.clientY
    }, { signal })
    const endPan = (e: PointerEvent) => {
        panning = false
        if (canvas.hasPointerCapture(e.pointerId)) canvas.releasePointerCapture(e.pointerId)
    }
    canvas.addEventListener('pointerup', endPan, { signal })
    canvas.addEventListener('pointercancel', endPan, { signal })

    watchPixelRatio(app, signal)
}

function clamp(z: number) {
    return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z))
}

/**
 * Browser zoom (Cmd +/-) and dragging the window to a different-density monitor both
 * change devicePixelRatio. The renderer captured the OLD value at init, so without
 * this the browser just upscales a too-small canvas and everything goes soft.
 *
 * There is no 'dprchange' event, so the trick is a media query that matches only the
 * CURRENT ratio: the moment it stops matching, re-arm at the new one.
 */
function watchPixelRatio(app: Application, signal: AbortSignal) {
    const arm = () => {
        matchMedia(`(resolution: ${devicePixelRatio}dppx)`).addEventListener('change', () => {
            if (signal.aborted) return
            app.renderer.resolution = devicePixelRatio
            app.renderer.resize(innerWidth, innerHeight)
            arm()
        }, { once: true, signal })
    }
    arm()
}
