#!/usr/bin/env python3
"""
SolarPunk Grant Pool Agent
Decentralized micro-grant system with community voting, transparent tracking,
and automated grants.gov RSS scanning.

AGPL-3.0 -- https://github.com/meekotharaccoon-cell/solarpunk-grants
"""

import json
import os
import sys
import time
import hashlib
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path

# -- Paths -----------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
APPLICATIONS_FILE = DATA_DIR / "applications.json"
POOL_FILE = DATA_DIR / "pool.json"
PROPOSALS_FILE = DATA_DIR / "proposals.json"
FUNDED_MD = ROOT / "FUNDED.md"

# -- Config ----------------------------------------------------------------
GRANTS_GOV_RSS = "https://www.grants.gov/rss/GG_NewOppByCategory.xml"
RELEVANT_KEYWORDS = [
    "community", "humanitarian", "technology", "education", "health",
    "environment", "equity", "justice", "infrastructure", "resilience",
    "renewable", "solar", "mutual aid", "cooperative", "civic",
    "disability", "housing", "food security", "water", "sanitation",
]
VOTING_PERIOD_DAYS = 7
MIN_VOTES_TO_PASS = 3
GITHUB_REPO = "meekotharaccoon-cell/solarpunk-grants"


def ensure_data_dir():
    """Create data directory if it does not exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default=None):
    """Load JSON file, return default if missing or corrupt."""
    if default is None:
        default = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: Path, data):
    """Write JSON file with pretty formatting."""
    ensure_data_dir()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)


# -- Grants.gov RSS Scraper ------------------------------------------------
class GrantsScraper:
    """Scrape grants.gov RSS feed for relevant humanitarian/community/tech grants."""

    def __init__(self):
        self.seen_ids: set = set()
        self._load_seen()

    def _load_seen(self):
        apps = load_json(APPLICATIONS_FILE, {"scraped": [], "tracked": []})
        self.seen_ids = {g.get("id") for g in apps.get("scraped", [])}

    def fetch_rss(self) -> list:
        """Fetch and parse grants.gov RSS feed."""
        try:
            req = urllib.request.Request(
                GRANTS_GOV_RSS,
                headers={"User-Agent": "SolarPunkGrantAgent/1.0"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                xml_data = resp.read()
            root = ET.fromstring(xml_data)
            items = []
            for item in root.iter("item"):
                title = item.findtext("title", "")
                link = item.findtext("link", "")
                desc = item.findtext("description", "")
                pub_date = item.findtext("pubDate", "")
                grant_id = hashlib.sha256(
                    (title + link).encode()
                ).hexdigest()[:12]
                items.append({
                    "id": grant_id,
                    "title": title.strip(),
                    "link": link.strip(),
                    "description": desc.strip()[:500],
                    "pub_date": pub_date.strip(),
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                })
            return items
        except Exception as e:
            print(f"[WARN] RSS fetch failed: {e}")
            return []

    def filter_relevant(self, items: list) -> list:
        """Filter grants matching humanitarian/community/tech keywords."""
        relevant = []
        for item in items:
            if item["id"] in self.seen_ids:
                continue
            text = (item["title"] + " " + item["description"]).lower()
            matched = [kw for kw in RELEVANT_KEYWORDS if kw in text]
            if matched:
                item["matched_keywords"] = matched
                relevant.append(item)
        return relevant

    def scrape(self) -> list:
        """Full scrape cycle: fetch, filter, persist."""
        print("[*] Fetching grants.gov RSS feed...")
        items = self.fetch_rss()
        print(f"    Found {len(items)} total items")
        relevant = self.filter_relevant(items)
        print(f"    {len(relevant)} new relevant grants")

        if relevant:
            apps = load_json(APPLICATIONS_FILE, {"scraped": [], "tracked": []})
            apps["scraped"].extend(relevant)
            apps["last_scrape"] = datetime.now(timezone.utc).isoformat()
            save_json(APPLICATIONS_FILE, apps)

        return relevant


# -- Community Micro-Grant Pool --------------------------------------------
class GrantPool:
    """Manage the community micro-grant pool: contributions and disbursements."""

    def __init__(self):
        self.data = load_json(POOL_FILE, {
            "balance_usd": 0.0,
            "contributions": [],
            "disbursements": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

    def contribute(self, donor: str, amount: float, note: str = "") -> dict:
        """Record a contribution to the pool."""
        entry = {
            "id": hashlib.sha256(
                f"{donor}{amount}{time.time()}".encode()
            ).hexdigest()[:10],
            "donor": donor,
            "amount": round(amount, 2),
            "note": note,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.data["balance_usd"] = round(
            self.data["balance_usd"] + amount, 2
        )
        self.data["contributions"].append(entry)
        self._save()
        print(f"[+] Contribution: ${amount:.2f} from {donor}")
        return entry

    def disburse(self, recipient: str, amount: float, proposal_id: str,
                 reason: str = "") -> dict:
        """Disburse funds from the pool to a grant recipient."""
        if amount > self.data["balance_usd"]:
            print(f"[!] Insufficient funds: ${self.data['balance_usd']:.2f} "
                  f"available, ${amount:.2f} requested")
            return None

        entry = {
            "id": hashlib.sha256(
                f"{recipient}{amount}{time.time()}".encode()
            ).hexdigest()[:10],
            "recipient": recipient,
            "amount": round(amount, 2),
            "proposal_id": proposal_id,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.data["balance_usd"] = round(
            self.data["balance_usd"] - amount, 2
        )
        self.data["disbursements"].append(entry)
        self._save()
        print(f"[-] Disbursement: ${amount:.2f} to {recipient}")
        return entry

    def get_balance(self) -> float:
        return self.data["balance_usd"]

    def _save(self):
        save_json(POOL_FILE, self.data)


# -- Voting System ---------------------------------------------------------
class VotingSystem:
    """
    Track proposals and votes. Designed to integrate with GitHub reactions:
    thumbs-up = approve, thumbs-down = reject.
    """

    def __init__(self):
        self.proposals = load_json(PROPOSALS_FILE, {"proposals": []})

    def get_active_proposals(self) -> list:
        """Return proposals still in voting period."""
        now = datetime.now(timezone.utc)
        active = []
        for p in self.proposals.get("proposals", []):
            if p.get("status") != "voting":
                continue
            deadline = datetime.fromisoformat(p["voting_deadline"])
            if now < deadline:
                active.append(p)
        return active

    def tally_votes(self, proposal_id: str) -> dict:
        """Count votes for a proposal."""
        for p in self.proposals.get("proposals", []):
            if p.get("id") == proposal_id:
                votes = p.get("votes", [])
                approve = sum(1 for v in votes if v["vote"] == "approve")
                reject = sum(1 for v in votes if v["vote"] == "reject")
                return {
                    "proposal_id": proposal_id,
                    "approve": approve,
                    "reject": reject,
                    "total": len(votes),
                    "passed": approve >= MIN_VOTES_TO_PASS and approve > reject,
                }
        return {"error": "proposal not found"}

    def close_expired(self, pool: GrantPool) -> list:
        """Auto-close proposals past their voting deadline, disburse if passed."""
        now = datetime.now(timezone.utc)
        closed = []
        for p in self.proposals.get("proposals", []):
            if p.get("status") != "voting":
                continue
            deadline = datetime.fromisoformat(p["voting_deadline"])
            if now >= deadline:
                tally = self.tally_votes(p["id"])
                if tally.get("passed"):
                    p["status"] = "funded"
                    result = pool.disburse(
                        recipient=p["applicant"],
                        amount=p["amount"],
                        proposal_id=p["id"],
                        reason=p["title"],
                    )
                    p["disbursement"] = result
                else:
                    p["status"] = "rejected"
                p["closed_at"] = now.isoformat()
                p["vote_tally"] = tally
                closed.append(p)

        if closed:
            save_json(PROPOSALS_FILE, self.proposals)

        return closed


# -- Transparency Report ---------------------------------------------------
def generate_funded_md(pool: GrantPool, proposals: dict):
    """Regenerate FUNDED.md with current disbursement data."""
    lines = [
        "# FUNDED Grants",
        "",
        "> Auto-generated transparency report. Updated by the SolarPunk Grant Agent.",
        "",
        f"**Pool Balance:** ${pool.get_balance():.2f}",
        "",
        "## Disbursements",
        "",
        "| Date | Recipient | Amount | Proposal | Reason |",
        "|------|-----------|--------|----------|--------|",
    ]

    for d in pool.data.get("disbursements", []):
        date = d.get("timestamp", "")[:10]
        lines.append(
            f"| {date} | {d['recipient']} | ${d['amount']:.2f} "
            f"| {d.get('proposal_id', 'N/A')} | {d.get('reason', '')} |"
        )

    lines.extend([
        "",
        "## Contributions",
        "",
        "| Date | Donor | Amount | Note |",
        "|------|-------|--------|------|",
    ])

    for c in pool.data.get("contributions", []):
        date = c.get("timestamp", "")[:10]
        lines.append(
            f"| {date} | {c['donor']} | ${c['amount']:.2f} "
            f"| {c.get('note', '')} |"
        )

    funded_count = sum(
        1 for p in proposals.get("proposals", [])
        if p.get("status") == "funded"
    )
    rejected_count = sum(
        1 for p in proposals.get("proposals", [])
        if p.get("status") == "rejected"
    )
    active_count = sum(
        1 for p in proposals.get("proposals", [])
        if p.get("status") == "voting"
    )

    total_contributed = sum(c["amount"] for c in pool.data.get("contributions", []))
    total_disbursed = sum(d["amount"] for d in pool.data.get("disbursements", []))

    lines.extend([
        "",
        "## Summary",
        "",
        f"- **Funded proposals:** {funded_count}",
        f"- **Rejected proposals:** {rejected_count}",
        f"- **Active votes:** {active_count}",
        f"- **Total contributions:** ${total_contributed:.2f}",
        f"- **Total disbursed:** ${total_disbursed:.2f}",
        f"- **Last updated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
    ])

    with open(FUNDED_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"[*] FUNDED.md updated ({funded_count} funded, {active_count} active)")


# -- Main Agent Loop -------------------------------------------------------
def run():
    """Execute the full grant agent cycle."""
    print("=" * 60)
    print("  SolarPunk Grant Pool Agent")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)

    ensure_data_dir()

    # 1. Scrape grants.gov
    scraper = GrantsScraper()
    new_grants = scraper.scrape()
    if new_grants:
        print(f"\n  New relevant grants found:")
        for g in new_grants[:5]:
            print(f"    - {g['title'][:80]}")
            print(f"      Keywords: {', '.join(g.get('matched_keywords', []))}")
            print(f"      Link: {g['link']}")

    # 2. Process pool
    pool = GrantPool()
    print(f"\n  Pool balance: ${pool.get_balance():.2f}")

    # 3. Close expired votes and disburse
    voting = VotingSystem()
    closed = voting.close_expired(pool)
    if closed:
        print(f"\n  Closed {len(closed)} proposals:")
        for p in closed:
            status = p["status"].upper()
            print(f"    [{status}] {p['title']} -- ${p['amount']:.2f}")

    # 4. Report active proposals
    active = voting.get_active_proposals()
    if active:
        print(f"\n  {len(active)} active proposals in voting:")
        for p in active:
            tally = voting.tally_votes(p["id"])
            print(f"    - {p['title']} (${p['amount']:.2f}) "
                  f"[+{tally['approve']}/-{tally['reject']}]")

    # 5. Generate transparency report
    generate_funded_md(pool, voting.proposals)

    print("\n" + "=" * 60)
    print("  Cycle complete.")
    print("=" * 60)


if __name__ == "__main__":
    run()
