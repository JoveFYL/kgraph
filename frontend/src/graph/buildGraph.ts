import { csvParse } from 'd3-dsv'
import type { Graph, GNode, GLink } from './types'
import { AGENCY_COLOR, COLOR, JOB_COLOR, TASK_R } from './config'

/**
 * The columns we actually read out of Fractionalised_jobs.csv. The file has 21;
 * the other 15 (Tool_*, Subtask_*, reasoning) aren't needed to draw the graph.
 */
interface Row {
    Original_Agency: string
    Original_Job_Title: string
    Task_Line: string
    AI_Integration_Probability_Percent: string
    AI_Integration_Skill_Level: string
}

/**
 * The CSV has no agency_label column, only a 0-100 probability. These thresholds
 * are the ones the `agency_label` generated column in schema.sql uses, so the
 * frontend and the database can never disagree about what colour a task is.
 */
export function agencyLabel(pct: number) {
    if (pct < 20) return 'human-centric'
    if (pct < 60) return 'AI-augmented'
    return 'fully-automated'
}

/**
 * Raw CSV text -> {nodes, links, neighbours}.
 *
 * The tree is implied by repetition: there is no "job" record anywhere in the file,
 * just a job title that repeats once per task line. We discover the tiers by noting
 * first sightings, then immediately flatten back to two arrays — neither d3-force
 * nor Pixi can consume nested JSON.
 */
export function buildGraph(csvText: string): Graph {
    // The file is saved with a UTF-8 BOM (U+FEFF), an invisible character that would
    // otherwise be glued to the front of the first column's name.
    const rows = csvParse(csvText.replace(/^\uFEFF/, '')) as unknown as Row[]

    const nodes: GNode[] = []
    const links: GLink[] = []
    const agencies = new Set<string>()
    const jobs = new Set<string>()
    let taskSeq = 0

    for (const r of rows) {
        // 1,508 of 2,954 rows are not tasks — they're JD boilerplate like "Able to
        // work independently", scored 0% with skill level NA. Left in, they'd all
        // land under 20% and flood the graph green, making automation exposure look
        // like a non-problem. This filter is what makes the colours honest.
        if (r.AI_Integration_Skill_Level === 'NA') continue

        const agency = r.Original_Agency?.trim()
        const title = r.Original_Job_Title?.trim()
        const line = r.Task_Line?.trim()
        if (!agency || !title || !line) continue

        const aKey = `agency:${agency}`
        if (!agencies.has(aKey)) {
            agencies.add(aKey)
            nodes.push({
                id: aKey, tier: 'agency', agency,
                color: AGENCY_COLOR, radius: 0, label: agency,
            })
        }

        // Composite key: agency + title, never the title alone. In this file the 208
        // titles happen not to collide (they're prefixed "[LTA-...]"), but keying on
        // the bare string is the bug that silently merges two unrelated jobs and
        // invents a cross-agency link that isn't in the data. Department names in
        // this same CSV *do* collide — 23 names across 35 real departments.
        const jKey = `job:${agency}::${title}`
        if (!jobs.has(jKey)) {
            jobs.add(jKey)
            nodes.push({
                id: jKey, tier: 'job', agency,
                color: JOB_COLOR, radius: 0, label: title, sub: agency,
            })
            links.push({ source: jKey, target: aKey })
        }

        const pct = Number(r.AI_Integration_Probability_Percent) || 0
        const label = agencyLabel(pct)
        const tKey = `task:${taskSeq++}`
        nodes.push({
            id: tKey, tier: 'task', agency, color: COLOR[label], radius: TASK_R,
            label: line, sub: `${label} · ${pct}% AI integration`,
        })
        links.push({ source: tKey, target: jKey })
    }

    sizeHubs(nodes, links)
    const byId = new Map(nodes.map(n => [n.id, n]))
    return { nodes, links, byId, neighbours: buildAdjacency(links) }
}

/**
 * Hubs are sized by how many children hang off them, via sqrt so a 30-task job
 * isn't 10x the *area* of a 3-task one — the same trick Obsidian uses.
 */
function sizeHubs(nodes: GNode[], links: GLink[]) {
    const degree = new Map<string, number>()
    for (const l of links) {
        const t = l.target as string
        degree.set(t, (degree.get(t) ?? 0) + 1)
    }
    for (const n of nodes) {
        const d = Math.sqrt(degree.get(n.id) ?? 1)
        if (n.tier === 'agency') n.radius = 9 + d * 2.2
        else if (n.tier === 'job') n.radius = 2.6 + d * 1.4
    }
}

function buildAdjacency(links: GLink[]) {
    const neighbours = new Map<string, Set<string>>()
    const touch = (a: string, b: string) => {
        let s = neighbours.get(a)
        if (!s) neighbours.set(a, (s = new Set<string>()))
        s.add(b)
    }
    for (const l of links) {
        touch(l.source as string, l.target as string)
        touch(l.target as string, l.source as string)
    }
    return neighbours
}
