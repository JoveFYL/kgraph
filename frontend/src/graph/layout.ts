import {
    forceSimulation, forceManyBody, forceLink, forceCollide, forceX, forceY,
    type Simulation,
} from 'd3-force'
import type { Anchors, Graph, GNode, GLink } from './types'
import {
    ANCHOR_PULL, CHARGE, CHARGE_RANGE, IDLE_ALPHA, SEED_SPREAD, SLOTS, SPRING,
} from './config'

/**
 * Tiny deterministic PRNG (mulberry32). Using this instead of Math.random means the
 * seeded layout is identical on every reload, so force tweaks are actually comparable.
 */
function mulberry32(seed: number) {
    return () => {
        seed = (seed + 0x6d2b79f5) | 0
        let t = Math.imul(seed ^ (seed >>> 15), 1 | seed)
        t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
        return ((t ^ (t >>> 14)) >>> 0) / 4294967296
    }
}

/**
 * Give each agency its own point on the canvas.
 *
 * The four agencies share no links at all — this graph is four separate trees — so
 * nothing holds them in relation to each other. Slots are handed out biggest cluster
 * first, so the layout doesn't depend on which agency appeared first in the CSV.
 */
export function assignAnchors(nodes: GNode[], width: number, height: number): Anchors {
    const size = new Map<string, number>()
    for (const n of nodes) size.set(n.agency, (size.get(n.agency) ?? 0) + 1)

    const anchors: Anchors = new Map()
    ;[...size.entries()]
        .sort((a, b) => b[1] - a[1])
        .forEach(([agency], i) => {
            const [fx, fy] = SLOTS[i % SLOTS.length]
            anchors.set(agency, [width * fx, height * fy])
        })
    return anchors
}

/**
 * Scatter each node near its agency's anchor.
 *
 * d3's default starting layout is a spiral around (0,0), so without seeding, every
 * node sprints across the screen before settling. Nothing is pinned with fx/fy: the
 * anchor force below holds the clusters in place, which leaves every node — hubs
 * included — free to be pushed around and dragged.
 */
export function seedPositions(nodes: GNode[], anchors: Anchors, seed = 42) {
    const rand = mulberry32(seed)
    for (const n of nodes) {
        const [ax, ay] = anchors.get(n.agency)!
        // Hubs start exactly on their anchor; the rest scatter around it.
        const spread = n.tier === 'agency' ? 0 : SEED_SPREAD
        n.x = ax + (rand() - 0.5) * spread
        n.y = ay + (rand() - 0.5) * spread
    }
}

export function createSimulation({ nodes, links }: Graph, anchors: Anchors) {
    return forceSimulation<GNode>(nodes)
        // distanceMax is the important part: without it all 1,658 nodes repel all
        // the others, which inflates every cluster until all four merge into a blob.
        .force('charge', forceManyBody<GNode>()
            .strength(d => CHARGE[d.tier])
            .distanceMax(CHARGE_RANGE))
        .force('link', forceLink<GNode, GLink>(links)
            .id(d => d.id)
            .distance(l => SPRING[springKey(l)].distance)
            .strength(l => SPRING[springKey(l)].strength))
        // No forceCenter. It recentres the *average* of all four clusters onto a
        // single point — exactly the blob we're avoiding. Each node is instead pulled
        // toward its own agency's anchor, which is what keeps the four apart.
        .force('x', forceX<GNode>(d => anchors.get(d.agency)![0]).strength(ANCHOR_PULL))
        .force('y', forceY<GNode>(d => anchors.get(d.agency)![1]).strength(ANCHOR_PULL))
        .force('collide', forceCollide<GNode>(d => d.radius + 1.5).iterations(1))
        // Hold a trickle of energy forever so the physics never switches off. Without
        // this, alpha decays past alphaMin and d3 stops ticking entirely.
        .alphaTarget(IDLE_ALPHA)
}

/** job -> agency links are long and loose; task -> job links are short and stiff. */
function springKey(l: GLink): 'agency' | 'job' {
    return (l.target as GNode).tier === 'agency' ? 'agency' : 'job'
}

export type GraphSimulation = Simulation<GNode, GLink>
