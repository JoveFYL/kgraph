import type { Graphics } from 'pixi.js'
import type { SimulationNodeDatum, SimulationLinkDatum } from 'd3-force'

/** Which level of the tree a node sits at. */
export type Tier = 'agency' | 'job' | 'task'

export interface GNode extends SimulationNodeDatum {
    id: string          // unique key, e.g. "task:42" or "agency:Land Transport Authority"
    tier: Tier
    agency: string      // which cluster this node belongs to — drives its anchor
    color: number       // Pixi colour, e.g. 0xf5a524
    radius: number
    label: string       // main tooltip line
    sub?: string        // second, dimmer tooltip line
    gfx?: Graphics      // the Pixi circle this node is drawn as
}

/**
 * A spring between two nodes. source/target start as ids (strings); d3 REPLACES
 * them with the actual GNode objects once the simulation starts.
 */
export interface GLink extends SimulationLinkDatum<GNode> {
    source: string | GNode
    target: string | GNode
}

export interface Graph {
    nodes: GNode[]
    links: GLink[]
    /** id -> ids it is linked to. Used by the hover highlight. */
    neighbours: Map<string, Set<string>>
    byId: Map<string, GNode>
}

/** Where each agency's cluster is centred on the canvas. */
export type Anchors = Map<string, [number, number]>
