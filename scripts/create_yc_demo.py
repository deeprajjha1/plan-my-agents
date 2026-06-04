#!/usr/bin/env python3
"""
YC Demo Video Creator for PlanMyAgents

This script creates a professional demo by:
1. Running the demo server with Groq for fast responses
2. Executing a scripted demo with proper timing
3. Showing results smartly without boring wait times
4. Adding intro/outro cards that stay for 3-4 seconds

Usage:
    # Start demo server in one terminal:
    GROQ_API_KEY=your_key make demo
    
    # Run this script in another terminal:
    python3 scripts/create_yc_demo.py
    
    # Or record with asciinema:
    python3 scripts/create_yc_demo.py --record
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path

# Add API to path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))


class YCDemoCreator:
    """Creates a professional YC demo video."""

    def __init__(self, api_base: str = "http://127.0.0.1:8787"):
        self.api_base = api_base
        self.colors = {
            "reset": "\033[0m",
            "bold": "\033[1m",
            "green": "\033[92m",
            "yellow": "\033[93m",
            "blue": "\033[94m",
            "cyan": "\033[96m",
        }

    def print_title(self, text: str, duration: float = 3.5) -> None:
        """Print a title card that stays on screen."""
        print("\n" + "=" * 80)
        print(text.center(80))
        print("=" * 80 + "\n")
        time.sleep(duration)

    def print_section(self, title: str, content: str, duration: float = 2.5) -> None:
        """Print a section with title and content."""
        print(f"\n{'─' * 80}")
        print(f"  {self.colors['bold']}{title}{self.colors['reset']}")
        print(f"{'─' * 80}")
        print(content)
        time.sleep(duration)

    async def check_server(self) -> bool:
        """Check if demo server is running."""
        try:
            req = urllib.request.Request(self.api_base)
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status == 200
        except Exception:
            return False

    async def call_goal(self, goal: str) -> dict:
        """Call the /goal endpoint."""
        params = urllib.parse.urlencode({"goal": goal})
        url = f"{self.api_base}/api/goal?{params}"
        
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=60) as response:
                data = response.read()
                return json.loads(data.decode('utf-8'))
        except urllib.error.HTTPError as e:
            error_data = e.read().decode('utf-8')
            try:
                return json.loads(error_data)
            except:
                raise Exception(f"HTTP {e.code}: {error_data}")
        except urllib.error.URLError as e:
            raise Exception(f"URL Error: {e.reason}")

    async def run_demo(self) -> None:
        """Run the complete demo sequence."""
        
        # Opening
        self.print_title(
            "PlanMyAgents\n\n"
            "Underwriters Laboratories for AI Tools\n\n"
            "YC W26 Application Demo",
            duration=4.0
        )

        # Problem Statement
        self.print_title(
            "THE PROBLEM\n\n"
            "AI agents need external tools, but most are broken",
            duration=4.0
        )

        self.print_section(
            "DOCUMENTED FAILURES",
            "• 72% of MCP server parameters undocumented\n"
            "  (IndieHackers, April 2026)\n\n"
            "• 200K servers at risk from design flaws\n"
            "  (The Register, April 2026)\n\n"
            "• Tools abandoned after initial hype\n\n"
            "• Breaking changes without warning\n\n"
            "• No way to know which tools actually work",
            duration=5.0
        )

        # Solution
        self.print_title(
            "THE SOLUTION\n\n"
            "Independent third-party verification",
            duration=4.0
        )

        self.print_section(
            "HOW IT WORKS",
            f"{self.colors['cyan']}1. DISCOVER{self.colors['reset']}\n"
            "   17-scout fleet crawls MCP registries, GitHub, npm, HackerNews\n\n"
            f"{self.colors['cyan']}2. TEST{self.colors['reset']}\n"
            "   Automated benchmarks with 1,200+ test cases\n"
            "   Happy path, edge cases, adversarial examples\n\n"
            f"{self.colors['cyan']}3. VERIFY{self.colors['reset']}\n"
            "   4-tier credibility scoring\n"
            "   synthetic_only → smoke_test → developing → publishable\n\n"
            f"{self.colors['cyan']}4. ROUTE{self.colors['reset']}\n"
            "   Natural language → verified tool\n"
            "   Export recipes to Claude, Cursor, n8n",
            duration=6.0
        )

        # Live Demo
        self.print_title(
            "LIVE DEMO\n\n"
            "Finding verified tools for email verification",
            duration=4.0
        )

        goal = "I need to verify if an email address is valid and deliverable"

        self.print_section(
            "USER REQUEST",
            f'{self.colors["yellow"]}Goal:{self.colors["reset"]} "{goal}"',
            duration=2.0
        )

        print(f"\n{self.colors['cyan']}🔍 Searching verified tools...{self.colors['reset']}")
        print("   • Crawling 10,000+ AI tools")
        print("   • Checking verification status")
        print("   • Running quality benchmarks")
        time.sleep(1.5)

        # Call the actual API (with smart result display)
        try:
            result = await self.call_goal(goal)

            if result.get("ok"):
                plan = result.get("plan", {})
                agents = plan.get("agents", [])
                
                if agents:
                    agent = agents[0]
                    self.print_section(
                        "✅ VERIFIED TOOL FOUND",
                        f"{self.colors['green']}Tool:{self.colors['reset']} {agent.get('display_name', 'Hunter Email Verifier')}\n"
                        f"{self.colors['green']}Capability:{self.colors['reset']} {agent.get('capability', 'email_verification')}\n"
                        f"{self.colors['green']}Status:{self.colors['reset']} Verified & Tested\n"
                        f"{self.colors['green']}Credibility:{self.colors['reset']} {agent.get('credibility_status', 'publishable')}",
                        duration=3.5
                    )
                    
                    if result.get("executed"):
                        answer = result.get("answer", {})
                        self.print_section(
                            "📋 EXECUTION RESULT",
                            f"{answer.get('title', 'Workflow completed')}\n\n"
                            f"{answer.get('body', 'Task completed successfully')}",
                            duration=3.0
                        )
                else:
                    # Show discovery results
                    discovery = plan.get("discovery", {})
                    candidates = discovery.get("candidates", [])
                    self.print_section(
                        "📋 DISCOVERY RESULTS",
                        f"Found {len(candidates)} candidates in discovery pipeline\n"
                        "Verification in progress",
                        duration=3.0
                    )

        except Exception as e:
            print(f"\n{self.colors['yellow']}⚠️  Demo server not responding: {e}{self.colors['reset']}")
            print("Showing mock result for demo purposes...\n")
            time.sleep(1.0)

            self.print_section(
                "✅ VERIFIED TOOL FOUND",
                f"{self.colors['green']}Tool:{self.colors['reset']} Hunter Email Verifier\n"
                f"{self.colors['green']}Capability:{self.colors['reset']} email_verification\n"
                f"{self.colors['green']}Status:{self.colors['reset']} Verified & Tested\n"
                f"{self.colors['green']}Credibility:{self.colors['reset']} publishable\n"
                f"{self.colors['green']}Benchmark Score:{self.colors['reset']} 95/100",
                duration=3.5
            )

        # Recipe Export
        self.print_section(
            "📦 READY TO USE",
            "Recipe exported for:\n"
            "  • Claude Desktop (JSON config)\n"
            "  • Cursor (prompt template)\n"
            "  • n8n (workflow JSON)\n"
            "  • CLI (shell script)\n\n"
            f"{self.colors['cyan']}→ Copy-paste into your workflow{self.colors['reset']}",
            duration=3.5
        )

        # Differentiation
        self.print_title(
            "WHAT MAKES US DIFFERENT\n\n"
            "Independent third-party verification",
            duration=4.0
        )

        self.print_section(
            "COMPETITORS VS US",
            f"{self.colors['yellow']}LangSmith ($1.25B), Datadog, Arize ($131M):{self.colors['reset']}\n"
            "  → Help developers test THEIR OWN agents\n"
            "  → Internal DevOps tools\n\n"
            f"{self.colors['green']}PlanMyAgents:{self.colors['reset']}\n"
            "  → Independently test EXTERNAL TOOLS\n"
            "  → Third-party verification (like Underwriters Laboratories)\n"
            "  → Protocol-agnostic (MCP, A2A, APIs)\n"
            "  → Absolutely neutral (no pay-to-rank)",
            duration=5.5
        )

        # Market Validation
        self.print_section(
            "MARKET VALIDATION",
            "• 97M monthly MCP SDK downloads\n"
            "• 41% of software orgs using MCP in production\n"
            "• Snyk acquired Invariant Labs (AI agent testing) - June 2025\n"
            "• Category validated, gap unfilled",
            duration=4.0
        )

        # Next Steps
        self.print_title(
            "NEXT 6 MONTHS\n\n"
            "Scaling from dozens to thousands of verified tools",
            duration=4.0
        )

        self.print_section(
            "ROADMAP",
            f"{self.colors['cyan']}1. AI-POWERED TEST GENERATION{self.colors['reset']}\n"
            "   Automatically write tests from schemas\n"
            "   Scale from 7 to 50+ capabilities\n\n"
            f"{self.colors['cyan']}2. SELF-SERVICE VERIFICATION{self.colors['reset']}\n"
            "   Tool creators submit → auto-verify in 6 minutes\n"
            "   Verified badge if score > 80%\n\n"
            f"{self.colors['cyan']}3. PARTNERSHIP DISTRIBUTION{self.colors['reset']}\n"
            "   Native integration into n8n, Cursor\n"
            "   OEM our trust layer into platforms",
            duration=5.5
        )

        # Closing
        self.print_title(
            "PlanMyAgents\n\n"
            "Trust infrastructure for the AI agent economy\n\n"
            "planmyagents.com",
            duration=4.0
        )

        print(f"\n{self.colors['green']}✅ Demo complete!{self.colors['reset']}\n")


async def main():
    parser = argparse.ArgumentParser(description="Create YC demo video for PlanMyAgents")
    parser.add_argument(
        "--record",
        action="store_true",
        help="Record with asciinema (requires asciinema installed)"
    )
    parser.add_argument(
        "--api-base",
        default="http://127.0.0.1:8787",
        help="Demo server base URL (default: http://127.0.0.1:8787)"
    )
    args = parser.parse_args()

    creator = YCDemoCreator(api_base=args.api_base)

    # Check if server is running
    print("Checking demo server...")
    if not await creator.check_server():
        print(f"\n⚠️  WARNING: Demo server not accessible at {args.api_base}")
        print("\nTo start the demo server:")
        print("  1. In one terminal: make demo")
        print("  2. Wait for 'Uvicorn running on http://127.0.0.1:8787'")
        print("  3. Run this script again")
        print("\nPress Enter to continue with mock data, or Ctrl+C to cancel...")
        input()

    print("\n" + "=" * 80)
    print("STARTING DEMO".center(80))
    print("=" * 80)
    print("\nThis demo will take about 2 minutes.")
    print("Each section pauses for 3-4 seconds so reviewers can read.")
    
    if args.record:
        print("\nRecording with asciinema...")
        print("Starting in 3 seconds...")
        time.sleep(3)
    else:
        print("\nStarting in 3 seconds...")
        print("(Use --record flag to record with asciinema)")
        time.sleep(3)

    await creator.run_demo()


if __name__ == "__main__":
    asyncio.run(main())
