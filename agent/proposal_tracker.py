#!/usr/bin/env python3
"""
SolarPunk Proposal Tracker
Create, vote on, and manage community micro-grant proposals.
All state stored in JSON -- no database required.

AGPL-3.0 -- https://github.com/meekotharaccoon-cell/solarpunk-grants
"""

import json
import hashlib
import time
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# -- Paths -----------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
PROPOSALS_FILE = DATA_DIR / "proposals.json"
FUNDED_MD = ROOT / "FUNDED.md"

VOTING_PERIOD_DAYS = 7
MIN_VOTES_TO_PASS = 3


def ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_proposals() -> dict:
    try:
        with open(PROPOSALS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"proposals": []}


def save_proposals(data: dict):
    ensure_data_dir()
    with open(PROPOSALS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)


# -- Proposal CRUD ---------------------------------------------------------
def create_proposal(title: str, amount: float, description: str,
                    applicant: str) -> dict:
    """Create a new grant proposal and open it for voting."""
    data = load_proposals()
    now = datetime.now(timezone.utc)

    proposal = {
        "id": hashlib.sha256(
            f"{title}{applicant}{time.time()}".encode()
        ).hexdigest()[:12],
        "title": title,
        "amount": round(amount, 2),
        "description": description,
        "applicant": applicant,
        "status": "voting",
        "created_at": now.isoformat(),
        "voting_deadline": (now + timedelta(days=VOTING_PERIOD_DAYS)).isoformat(),
        "votes": [],
        "vote_tally": None,
        "closed_at": None,
        "disbursement": None,
    }

    data["proposals"].append(proposal)
    save_proposals(data)
    print(f"[+] Proposal created: {proposal['id']} -- {title}")
    print(f"    Amount: ${amount:.2f}")
    print(f"    Applicant: {applicant}")
    print(f"    Voting deadline: {proposal['voting_deadline']}")
    return proposal


def cast_vote(proposal_id: str, voter: str, vote: str) -> dict:
    """
    Cast a vote on a proposal.
    vote must be 'approve' or 'reject'.
    Each voter gets one vote; re-voting overwrites the previous vote.
    """
    if vote not in ("approve", "reject"):
        return {"error": "vote must be 'approve' or 'reject'"}

    data = load_proposals()
    for p in data["proposals"]:
        if p["id"] == proposal_id:
            if p["status"] != "voting":
                return {"error": f"proposal is {p['status']}, not accepting votes"}

            deadline = datetime.fromisoformat(p["voting_deadline"])
            if datetime.now(timezone.utc) >= deadline:
                return {"error": "voting period has ended"}

            # Remove previous vote by this voter (allows vote changes)
            p["votes"] = [v for v in p["votes"] if v["voter"] != voter]

            vote_entry = {
                "voter": voter,
                "vote": vote,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            p["votes"].append(vote_entry)
            save_proposals(data)
            print(f"[*] Vote recorded: {voter} -> {vote} on {proposal_id}")
            return vote_entry

    return {"error": "proposal not found"}


def get_proposal(proposal_id: str) -> dict:
    """Get a single proposal by ID."""
    data = load_proposals()
    for p in data["proposals"]:
        if p["id"] == proposal_id:
            return p
    return {"error": "proposal not found"}


def list_proposals(status: str = None) -> list:
    """List all proposals, optionally filtered by status."""
    data = load_proposals()
    proposals = data.get("proposals", [])
    if status:
        proposals = [p for p in proposals if p.get("status") == status]
    return proposals


def close_proposal(proposal_id: str, force_status: str = None) -> dict:
    """
    Manually close a proposal. If force_status is not provided,
    the vote tally determines the outcome.
    """
    data = load_proposals()
    for p in data["proposals"]:
        if p["id"] == proposal_id:
            if p["status"] != "voting":
                return {"error": f"proposal already closed with status: {p['status']}"}

            votes = p.get("votes", [])
            approve = sum(1 for v in votes if v["vote"] == "approve")
            reject = sum(1 for v in votes if v["vote"] == "reject")
            passed = approve >= MIN_VOTES_TO_PASS and approve > reject

            if force_status:
                p["status"] = force_status
            elif passed:
                p["status"] = "funded"
            else:
                p["status"] = "rejected"

            p["closed_at"] = datetime.now(timezone.utc).isoformat()
            p["vote_tally"] = {
                "approve": approve,
                "reject": reject,
                "total": len(votes),
                "passed": passed,
            }

            save_proposals(data)
            print(f"[*] Proposal {proposal_id} closed: {p['status']}")
            return p

    return {"error": "proposal not found"}


# -- FUNDED.md Generator ---------------------------------------------------
def generate_funded_md():
    """Generate updated FUNDED.md from funded proposals."""
    data = load_proposals()
    funded = [p for p in data.get("proposals", []) if p.get("status") == "funded"]
    active = [p for p in data.get("proposals", []) if p.get("status") == "voting"]
    rejected = [p for p in data.get("proposals", []) if p.get("status") == "rejected"]

    lines = [
        "# FUNDED Grants",
        "",
        "> Auto-generated transparency report. Updated by the SolarPunk Grant Agent.",
        "",
        "## Funded Proposals",
        "",
        "| Date | Title | Applicant | Amount | Votes |",
        "|------|-------|-----------|--------|-------|",
    ]

    for p in funded:
        date = (p.get("closed_at") or p.get("created_at", ""))[:10]
        tally = p.get("vote_tally", {})
        vote_str = f"+{tally.get('approve', 0)}/-{tally.get('reject', 0)}"
        lines.append(
            f"| {date} | {p['title']} | {p['applicant']} "
            f"| ${p['amount']:.2f} | {vote_str} |"
        )

    if active:
        lines.extend([
            "",
            "## Active Proposals (Voting Open)",
            "",
            "| Title | Applicant | Amount | Deadline | Current Votes |",
            "|-------|-----------|--------|----------|---------------|",
        ])
        for p in active:
            deadline = p.get("voting_deadline", "")[:10]
            votes = p.get("votes", [])
            approve = sum(1 for v in votes if v["vote"] == "approve")
            reject = sum(1 for v in votes if v["vote"] == "reject")
            lines.append(
                f"| {p['title']} | {p['applicant']} "
                f"| ${p['amount']:.2f} | {deadline} | +{approve}/-{reject} |"
            )

    lines.extend([
        "",
        "## Summary",
        "",
        f"- **Total funded:** {len(funded)}",
        f"- **Total rejected:** {len(rejected)}",
        f"- **Currently voting:** {len(active)}",
        f"- **Total requested (funded):** "
        f"${sum(p['amount'] for p in funded):.2f}",
        f"- **Last updated:** "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
    ])

    with open(FUNDED_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"[*] FUNDED.md regenerated: {len(funded)} funded, {len(active)} active")
    return FUNDED_MD


# -- CLI Interface ---------------------------------------------------------
def cli():
    """Simple CLI for proposal management."""
    if len(sys.argv) < 2:
        print("Usage: proposal_tracker.py <command> [args...]")
        print("")
        print("Commands:")
        print("  create <title> <amount> <description> <applicant>")
        print("  vote <proposal_id> <voter> <approve|reject>")
        print("  list [status]")
        print("  show <proposal_id>")
        print("  close <proposal_id> [force_status]")
        print("  report")
        return

    cmd = sys.argv[1]

    if cmd == "create":
        if len(sys.argv) < 6:
            print("Usage: create <title> <amount> <description> <applicant>")
            return
        create_proposal(
            title=sys.argv[2],
            amount=float(sys.argv[3]),
            description=sys.argv[4],
            applicant=sys.argv[5],
        )

    elif cmd == "vote":
        if len(sys.argv) < 5:
            print("Usage: vote <proposal_id> <voter> <approve|reject>")
            return
        result = cast_vote(sys.argv[2], sys.argv[3], sys.argv[4])
        if "error" in result:
            print(f"[!] {result['error']}")

    elif cmd == "list":
        status = sys.argv[2] if len(sys.argv) > 2 else None
        proposals = list_proposals(status)
        if not proposals:
            print("No proposals found.")
            return
        for p in proposals:
            votes = p.get("votes", [])
            approve = sum(1 for v in votes if v["vote"] == "approve")
            reject = sum(1 for v in votes if v["vote"] == "reject")
            print(f"  [{p['status'].upper():8s}] {p['id']} "
                  f"-- {p['title']} (${p['amount']:.2f}) "
                  f"[+{approve}/-{reject}]")

    elif cmd == "show":
        if len(sys.argv) < 3:
            print("Usage: show <proposal_id>")
            return
        p = get_proposal(sys.argv[2])
        if "error" in p:
            print(f"[!] {p['error']}")
        else:
            print(json.dumps(p, indent=2, default=str))

    elif cmd == "close":
        if len(sys.argv) < 3:
            print("Usage: close <proposal_id> [force_status]")
            return
        force = sys.argv[3] if len(sys.argv) > 3 else None
        result = close_proposal(sys.argv[2], force)
        if "error" in result:
            print(f"[!] {result['error']}")

    elif cmd == "report":
        generate_funded_md()

    else:
        print(f"Unknown command: {cmd}")


if __name__ == "__main__":
    cli()
