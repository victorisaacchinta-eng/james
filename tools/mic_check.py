"""Microphone stress check (iteration 2, P0-F). Run on the Mac, JAMES closed:

    python -m tools.mic_check                 # 15 open/record/close cycles, talkback OFF
    python -m tools.mic_check --talk          # the same, with JAMES's voice (neural, else say) before and during cycles

Compare the two summaries. If errors only appear with --talk, speech output and the mic are fighting over the
audio device; if they appear in both, look at the device line (Bluetooth headsets and Continuity mics switch).
Nothing is recorded to disk; only counts and error text are printed."""
from __future__ import annotations

import argparse
import time

import numpy as np

from agents.echo import Recorder


def run(cycles: int, talk: bool, secs: float) -> dict:
    rec = Recorder()
    ok = fail = 0
    errors: list[str] = []
    talker = None
    if talk:                                            # the voice JAMES really uses (neural if installed, else say)
        import talk as T
        prefs = T.load_prefs()
        talker = T.Talker(True, prefs.get("voice", ""), engine="auto", speed=prefs.get("speed", T.NEURAL_SPEED))
        if talker.neural is not None:
            talker.neural.ready.wait(30)
    print(f"device: {rec.device_name()}  talkback: {talker.describe() if talker else 'off'}  cycles: {cycles}")
    for n in range(1, cycles + 1):
        if talker is not None:                          # odd cycles: speak, then record (what JAMES does);
            talker.say("Checking the microphone. One, two, three.")   # even: record while it speaks (stress)
            if n % 2:
                time.sleep(0.2)
                while talker.speaking:
                    time.sleep(0.05)
            else:
                time.sleep(0.4)
        try:
            rec.start()
            time.sleep(secs)
            audio = rec.stop()
            level = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0
            ok += 1
            print(f"  {n:2d} ok    {audio.size / 16000:.1f}s  rms {level:.4f}")
        except Exception as e:  # noqa: BLE001
            fail += 1
            errors.append(str(e)[:100])
            print(f"  {n:2d} FAIL  {str(e)[:100]}")
        finally:
            if rec.active:                       # must never happen: stop() always releases
                print("  !! stream still open after a cycle")
            if talker is not None:
                talker.stop()
        time.sleep(0.3)
    summary = {"talk": talk, "ok": ok, "fail": fail, "stats": rec.stats, "errors": sorted(set(errors))}
    print(f"RESULT talkback={'on' if talk else 'off'}: {ok}/{cycles} ok, {fail} failed, stats={rec.stats}")
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycles", type=int, default=15)
    ap.add_argument("--talk", action="store_true")
    ap.add_argument("--secs", type=float, default=1.0)
    a = ap.parse_args()
    run(a.cycles, a.talk, a.secs)
