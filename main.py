#!/usr/bin/env python3
"""
Aegis — Autonomous AI Co-Founder for Attack Surface Management
==============================================================
Kane lives here. He monitors, sells, prices, negotiates,
and talks to you like a partner — not a tool.

Usage:
  python main.py              # Terminal chat mode
  python main.py --voice      # Voice interaction mode
  python main.py --daemon     # Background daemon with Telegram
  python main.py --once       # Run autonomous cycle once
"""

import argparse
import sys
import os
import threading
import time


def main():
    parser = argparse.ArgumentParser(description="Finch — Autonomous AI Co-Founder")
    parser.add_argument("--voice", action="store_true", help="Voice interaction mode")
    parser.add_argument("--daemon", action="store_true", help="Run as background daemon")
    parser.add_argument("--once", action="store_true", help="Run one autonomous cycle and exit")
    parser.add_argument("--config", default="./config.yaml", help="Config file path")
    args = parser.parse_args()

    # Change to finch directory
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    from core.daemon import FinchDaemon

    daemon = FinchDaemon(config_path=args.config)

    if args.once:
        daemon.autonomous_cycle()
        print(daemon.proactive_message())
        return

    if args.daemon:
        # Start email listener for cold email replies
        daemon.start_email_listener()

        # Start Telegram in background (optional — remove if you don't want it)
        telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if telegram_token:
            try:
                from messaging.telegram_bot import FinchTelegram
                tg = FinchTelegram(daemon=daemon)
                tg_thread = threading.Thread(target=tg.run, daemon=True)
                tg_thread.start()
                print(f"[{daemon.name}] Telegram bot started.")
            except Exception as e:
                print(f"[Finch] Telegram not available: {e}")
        else:
            print(f"[{daemon.name}] Telegram skipped — no TELEGRAM_BOT_TOKEN set.")

        # Schedule autonomous cycles
        from apscheduler.schedulers.background import BackgroundScheduler
        scheduler = BackgroundScheduler()
        scheduler.add_job(
            daemon.autonomous_cycle,
            "interval",
            hours=6,
            id="autonomous_cycle",
        )
        scheduler.start()

        print(f"\n[{daemon.name}] Daemon running. Autonomous cycles every 6 hours.")
        print(f"[{daemon.name}] Web dashboard: python web/chat_server.py")
        print(f"[{daemon.name}] {daemon.proactive_message()}")

        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            print(f"\n[{daemon.name}] Shutting down.")
            scheduler.shutdown()

    elif args.voice:
        daemon.run_voice_loop()

    else:
        daemon.run_terminal_loop()


if __name__ == "__main__":
    main()
