"""/redesign Step 1 -- Decompose.

One job description in, discrete task lines out. Runtime, not batch: no DB
writes, no worker pool, no resume key. Output feeds Step 2's predict().

The corpus was never split by a model -- task_line arrived as a column in the
source CSV (readData.py:72), and all 1452 of them are verbatim substrings of
their job_description. Step 2's K/FLOOR/STRIP_N were calibrated leave-one-out
inside that corpus, so this step EXTRACTS spans rather than writing task
statements: paraphrase would hand Step 2 a cleaner text distribution than the
one its thresholds were measured on.
"""
from pathlib import Path
from typing import NamedTuple
import json
import os
import sys
import unicodedata

from dotenv import load_dotenv
from openai import AzureOpenAI

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# On Azure, MODEL is your chat *deployment name*, not the underlying model name.
MODEL = os.getenv("AZURE_OPENAI_DEPLOYMENT")


class DecomposedTask(NamedTuple):
    """One candidate task line. Non-"ok" spans are returned too, not dropped --
    the caller filters, which keeps the quality gate auditable in the UI."""
    task_line: str
    task_quality: str    # 'ok' | 'vague' | 'boilerplate'
    verbatim: bool       # False = the model rewrote it; see check_verbatim()


def build_system_prompt() -> str:
    """Rubric wording, JSON-only instruction and few-shot format are lifted from
    enrich.py so the quality gate stays consistent between corpus and new input.
    The extraction step and the examples are new -- enrich.py never split
    anything, so there was nothing to copy."""
    return """You are an occupational analyst. You read a full job description \
and break it into the discrete tasks the role actually performs, for a workforce-redesign tool.

Work in this order:

1. Extract each task as a VERBATIM span of the job description. Copy the wording \
EXACTLY as it appears -- do not rephrase, summarise, merge two sentences, tidy grammar, \
or add words. Split where the description splits. A span may be a full sentence or a \
two-word phrase; match whatever the text gives you. Do not impose a uniform style.
   Extract every span that is ABOUT THE ROLE OR ITS ORGANISATION -- the duties themselves, \
the framing around them, and the description of the team or division. Do not judge a span \
here; judging happens in step 2, and a weak span must reach it. Skip a span ONLY if it \
names no work activity at all: bare entry requirements (degrees, licences, years of \
experience, software proficiency, personal qualities), benefits, and information about \
the hiring process. A span that names an activity is a task even when it is worded as a \
requirement -- "Able to lead case discussions with stakeholders" describes leading case \
discussions, so extract it; "Candidate must be passionate about the work" and "Good \
knowledge in Microsoft Office" name no activity, so skip them.
2. task_quality -- judge each extracted span:
    "ok"          a real, specific task describing actual work.
    "vague"       generic or catch-all ("other duties as required", "support the team");
                  also role framing that names no concrete work ("You will work with a \
team of engineers", "The projects you will be working on include"), and duties qualified \
as occasional ("... as required").
    "boilerplate" not a task at all: filler, legalese, mission statements, and \
descriptions of what the organisation, division or team does rather than what the \
post-holder does ("The Service Delivery Division delivers services directly to residents").
   Return non-"ok" spans too; do not silently drop them. Dropping a span is the one \
mistake you cannot make -- it leaves no trace for a reviewer.

Be exhaustive: every distinct task in the description should appear exactly once. \
Do not invent tasks the description does not state.

Reason internally, then output only the JSON. Examples (real descriptions from this \
corpus and their real task lines), shown in the exact format you must return:

<example>
Job description: "Degree in Life Sciences or related disciplines Able to work independently \
and in a team Able to systematically perform routine workflow with an eye for detail Good \
interpersonal and communication skills Good knowledge in Microsoft Office Physically fit and \
possess the physical endurance for the challenges of field work Valid Class 3 driver's licence \
(vehicle provided) Mosquito identification Sample processing Data entry Casual staff management \
Preparation and maintenance of logistics Vector surveillance Field studies"
{"tasks": [{"task_line": "Mosquito identification", "task_quality": "ok"}, {"task_line": "Sample processing", "task_quality": "ok"}, {"task_line": "Data entry", "task_quality": "ok"}, {"task_line": "Casual staff management", "task_quality": "ok"}, {"task_line": "Preparation and maintenance of logistics", "task_quality": "ok"}, {"task_line": "Vector surveillance", "task_quality": "ok"}, {"task_line": "Field studies", "task_quality": "ok"}]}
</example>

<example>
Job description: "You will be assisting Deputy Manager, Violation Management in the execution \
of daily operations of the Warrant Enforcement Section. Proficient in MS Office applications \
On-the-job training will be given on usage of internal systems You will be part of the warrant \
enforcement team within the Violation Management Division handling warrant of arrest (WA) matters. \
You will be required to assist the team in areas such as, but not limited to, administrative duties, \
counter operations and preparations of ops. You will also be required to handle phone/email \
enquiries with members of public, related to the WA issued. You may be required to liaise with \
internal and external stakeholders on WA-related matters."
{"tasks": [{"task_line": "You will be assisting Deputy Manager, Violation Management in the execution of daily operations of the Warrant Enforcement Section.", "task_quality": "vague"}, {"task_line": "You will be part of the warrant enforcement team within the Violation Management Division handling warrant of arrest (WA) matters.", "task_quality": "boilerplate"}, {"task_line": "You will be required to assist the team in areas such as, but not limited to, administrative duties, counter operations and preparations of ops.", "task_quality": "vague"}, {"task_line": "You will also be required to handle phone/email enquiries with members of public, related to the WA issued.", "task_quality": "ok"}, {"task_line": "You may be required to liaise with internal and external stakeholders on WA-related matters.", "task_quality": "ok"}]}
</example>
"""


# The machine contract. Property ORDER = the model's reasoning order: extract
# the span, then judge it. No maxItems -- the corpus tail reaches 45 tasks in a
# single JD, and truncating a long real description silently loses work.
RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "job_decomposition",
        "schema": {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "task_line": {"type": "string"},
                            "task_quality": {
                                "type": "string",
                                "enum": ["ok", "vague", "boilerplate"],
                            },
                        },
                        "required": ["task_line", "task_quality"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["tasks"],
            "additionalProperties": False,
        },
        "strict": True,
    },
}


# NFKC folds ligatures and non-breaking spaces but leaves curly punctuation alone,
# and 786 of the 1452 corpus rows contain a curly apostrophe -- so the model
# straightening one quote would fail a span that is genuinely verbatim.
_PUNCT = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                        "–": "-", "—": "-", "−": "-"})


def _norm(s: str) -> str:
    """Fold the differences that are NOT paraphrase: collapsed newlines and
    double spaces (JDs get pasted out of PDFs and web pages), and the unicode
    punctuation drift the model introduces."""
    return " ".join(unicodedata.normalize("NFKC", s).translate(_PUNCT).split()).lower()


def check_verbatim(task_line: str, job_description: str) -> bool:
    """The one rule the schema cannot enforce -- a paraphrase is still valid
    JSON. Whatever fails this after normalisation is a real rewrite: tidied
    grammar, an expanded fragment, or two bullets merged across a gap."""
    return _norm(task_line) in _norm(job_description)


def decompose(client: AzureOpenAI, job_description: str) -> list[DecomposedTask]:
    """One LLM call. Returns every candidate span, non-"ok" ones included.

    A non-verbatim span is kept and flagged, never dropped and never fatal:
    dropping loses a real task from a real person's job with no trace, while
    keeping costs one slightly-off embedding plus a reviewer-visible flag. Same
    asymmetry predict() encodes when it prefers a missed strip to a wrong one."""
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": build_system_prompt()},
            {"role": "user", "content": f"Job description: {job_description}"},
        ],
        response_format=RESPONSE_FORMAT,
        # Extraction has one right answer per JD, and re-running the same input
        # must not reshuffle which spans a reviewer sees.
        temperature=0,
    )
    result = json.loads(response.choices[0].message.content)
    return [
        DecomposedTask(
            task_line=t["task_line"],
            task_quality=t["task_quality"],
            verbatim=check_verbatim(t["task_line"], job_description),
        )
        for t in result["tasks"]
    ]


def main(path=None):
    """Drive one job description through the prompt. Reads a file if given, else
    stdin: `python decompose.py jd.txt` or `pbpaste | python decompose.py`."""
    job_description = Path(path).read_text() if path else sys.stdin.read()

    client = AzureOpenAI(
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        api_key=os.getenv("AZURE_OPENAI_API_KEY"),
        api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
        max_retries=5,
    )
    tasks = decompose(client, job_description)

    for t in tasks:
        flag = "" if t.verbatim else "   <-- NOT VERBATIM"
        print(f"  [{t.task_quality:11}] {t.task_line}{flag}")

    # A high rate is not a per-task problem -- it means the prompt is not
    # landing, and it is only visible as a rate.
    rewritten = sum(1 for t in tasks if not t.verbatim)
    ok = sum(1 for t in tasks if t.task_quality == "ok")
    print(f"\n{len(tasks)} spans ({ok} ok), {rewritten} non-verbatim")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
