/**
 * A plain HTML div floating over the canvas — deliberately not a Pixi object.
 * Text wrapping, fonts and CSS transitions are things the browser is already good
 * at, and a tooltip never needs to be part of the pan/zoom world.
 */
export function createTooltip(parent: HTMLElement) {
    const root = document.createElement('div')
    root.className = 'graph-tip'

    // Two child divs set via textContent — a task line containing "<" would be
    // interpreted as markup if we used innerHTML.
    const main = document.createElement('div')
    const sub = document.createElement('div')
    sub.className = 'graph-tip-sub'
    root.append(main, sub)
    parent.appendChild(root)

    const move = (x: number, y: number) => {
        root.style.left = `${x + 12}px`   // canvas pixel coords, offset off the cursor
        root.style.top = `${y + 12}px`
    }

    return {
        show(title: string, detail: string | undefined, x: number, y: number) {
            main.textContent = title
            sub.textContent = detail ?? ''
            move(x, y)
            root.style.opacity = '1'
        },
        move,
        hide: () => { root.style.opacity = '0' },
        destroy: () => root.remove(),
    }
}
