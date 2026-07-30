/** Every tunable number in one place, so tweaking the look never means reading logic. */

export const CSV_URL = '/Fractionalised_jobs.csv'

/** Canvas background. Also the colour of the ring drawn around each node. */
export const BG = 0x0e0f13

export const COLOR: Record<string, number> = {
    'human-centric': 0x30a46c,
    'AI-augmented': 0xf5a524,
    'fully-automated': 0xe5484d,
}
export const AGENCY_COLOR = 0xdfe3ea   // the 4 top-level hubs
export const JOB_COLOR = 0x7c8595      // the 208 job nodes

/**
 * Radius every circle is tessellated at before being scaled down to its real size.
 * Pixi picks a segment count from the radius you pass, so a 3.5px circle becomes a
 * visible polygon when zoomed. Drawing big then shrinking keeps it smooth at 12x.
 */
export const GEO = 64
export const TASK_R = 3.5
/** Ring thickness in screen px. */
export const RING = 1.6
/**
 * Extra hit-target padding in screen px. Keep this SMALL. Tasks sit about 10px apart
 * in a rosette, so a 3.5px dot padded by 5 gets an 8.5px hit radius -- the circles
 * overlap, the last-drawn node wins, and pressing a dot you can see grabs an
 * invisible neighbour instead. That reads as "some nodes aren't draggable".
 */
export const HIT_PAD = 1.5

export const MIN_ZOOM = 0.2
export const MAX_ZOOM = 12

// --- force tuning ---------------------------------------------------------------

/** How hard a node is pulled toward its own agency's anchor. Makes 4 clusters. */
export const ANCHOR_PULL = 0.01
/**
 * The simulation is never allowed to stop. d3 halts once `alpha` decays below
 * alphaMin (0.001), which is why the graph used to be inert unless you were holding
 * a node. Parking alphaTarget just above that keeps every node under the influence
 * of every force, permanently -- so nodes repel and shuffle whenever anything moves.
 * 0 restores the old behaviour of freezing after the first settle.
 */
export const IDLE_ALPHA = 0.02
/** Beyond this distance (world px) two nodes stop repelling. See createSimulation. */
export const CHARGE_RANGE = 300
export const CHARGE = { agency: -260, job: -70, task: -16 }
/** Link spring length and stiffness, by what the link points at. */
export const SPRING = {
    agency: { distance: 150, strength: 0.35 },  // job -> agency: long and loose
    job: { distance: 17, strength: 0.9 },       // task -> job: short and stiff
}

/**
 * Opacity of everything outside the hovered node's neighbourhood. Obsidian fades
 * context back rather than deleting it — at 0.06 the graph read as "everything
 * vanished", which loses the shape you were navigating by.
 */
export const DIM = 0.3
/** The whole background edge layer fades further, since 1,654 lines are the noise. */
export const EDGE_DIM = 0.18
/** How long the dim/undim takes, in milliseconds. Raise for a lazier fade. */
export const FADE_MS = 180

/**
 * How much energy to hold the simulation at while a node is being dragged.
 * 0 = the rest of the graph stays frozen; ~0.3 = neighbours follow responsively.
 */
export const DRAG_REHEAT = 0.5

/** Where the four clusters sit, as fractions of the viewport. */
export const SLOTS: [number, number][] = [
    [0.27, 0.29], [0.73, 0.29], [0.27, 0.74], [0.73, 0.74],
]
/** Spread of the initial random scatter around each anchor, in world px. */
export const SEED_SPREAD = 240

// --- edge styling ----------------------------------------------------------------

export const EDGE = {
    task: { width: 0.8, color: 0x5b6472, alpha: 0.22 },   // task -> job
    job: { width: 1.3, color: 0x9297a1, alpha: 0.4 },     // job -> agency
    hot: { width: 1.4, color: 0x00f0ff, alpha: 0.85 },    // the hovered node's links
}
