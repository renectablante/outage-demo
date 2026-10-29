"""
Outage Early-Warning: DEMO v2 with replayable timeline (ALL DATA INVENTED)

What this proves in miniature:
  1. Plain code (not the AI) counts the evidence and sets the confidence tier.
  2. The system gets more certain as evidence arrives over time (replay slider).
  3. Claude explains the evidence and drafts messages, but only when allowed.
  4. If Claude is unavailable or misbehaves, a plain template takes over.
  5. On thin evidence the system refuses to draft a customer message.
"""
import json
import random
import re

import streamlit as st

# Change this if your account says the model name is not found.
MODEL = "claude-sonnet-5-5"

# ---------- 1. INVENTED DATA: each scenario is a replayable timeline ----------
TICKET_POOL = {
    "A": ["Walang internet since 9:05, red light sa router",
          "Internet down, restarted twice, nothing",
          "No connection at all. Is the whole street affected?",
          "Wala pong signal, ilang minuto na",
          "Router blinking red, can't connect to anything"],
    "B": ["Router blinking red, can't connect to anything",
          "Slow internet lately, maybe Wi-Fi problem?",
          "Intermittent connection, drops every few minutes",
          "Hindi makapag-work from home, down ang net",
          "Connection keeps dropping since this morning"],
}


def frame(minute, silent, alarm, tickets, event, base, power=False, map_acc=None):
    f = dict(base)
    f.update(minute=minute, routers_silent=silent, alarm_seen=alarm,
             tickets_last_15min=tickets, power_utility_outage=power, event=event)
    if map_acc is not None:
        f["map_accuracy"] = map_acc
    return f


BASE_A = dict(routers_total=180, area="Cabinet 14, Riverside (fibre)", map_accuracy=0.95,
              similar_past_incidents=12, typical_repair_hours=2.5)
BASE_B = dict(routers_total=240, area="Tower 7, Hillside (fixed-wireless)", map_accuracy=0.70,
              similar_past_incidents=0, typical_repair_hours=None)

FRAMES = {
    "A. Clean fibre cut": [
        frame(0, 0, False, 0, "Normal operation", BASE_A),
        frame(5, 40, False, 4, "Some routers stop checking in; a few tickets", BASE_A),
        frame(10, 171, False, 14, "Nearly all routers behind Cabinet 14 are silent", BASE_A),
        frame(15, 171, False, 26, "Ticket spike confirms the pattern", BASE_A),
        frame(40, 171, True, 58, "NOC alarm finally appears (typical delay today, assumption A3)", BASE_A),
    ],
    "B. Murky tower problem": [
        frame(0, 0, False, 0, "Normal operation", BASE_B),
        frame(10, 30, False, 3, "A few routers go quiet", BASE_B),
        frame(20, 70, False, 9, "29% silent, a few tickets, no alarm. Could be many things", BASE_B),
        frame(30, 95, False, 15, "Still no alarm. Some routers may just be failing to report", BASE_B),
        frame(40, 180, False, 24, "Power utility reports an outage in the area", BASE_B, power=True),
        frame(45, 180, False, 27, "Field team confirms which customers sit on Tower 7",
              BASE_B, power=True, map_acc=0.92),
        frame(50, 180, True, 31, "NOC tower alarm finally appears", BASE_B, power=True, map_acc=0.92),
    ],
}
SCENARIOS = list(FRAMES)


# ---------- 2. PLAIN-CODE RULES (the AI never sets the tier) ----------
def score(ev):
    share = ev["routers_silent"] / ev["routers_total"]
    points, reasons = 0, []
    if share >= 0.7:
        points += 2
        reasons.append(f"{share:.0%} of routers silent (strong)")
    elif share >= 0.25:
        points += 1
        reasons.append(f"{share:.0%} of routers silent (partial: could be offline or just not reporting)")
    else:
        reasons.append(f"only {share:.0%} of routers silent")
    if ev["alarm_seen"]:
        points += 1
        reasons.append("network alarm present")
    else:
        reasons.append("no network alarm")
    if ev["tickets_last_15min"] >= 20:
        points += 1
        reasons.append(f"{ev['tickets_last_15min']} tickets in 15 min (spike)")
    else:
        reasons.append(f"{ev['tickets_last_15min']} tickets in 15 min (not a clear spike)")
    if ev["power_utility_outage"]:
        points += 1
        reasons.append("power utility confirms an outage in the area")
    if ev["map_accuracy"] >= 0.9:
        points += 1
        reasons.append(f"customer-to-equipment map {ev['map_accuracy']:.0%} accurate")
    else:
        reasons.append(f"customer-to-equipment map only {ev['map_accuracy']:.0%} accurate "
                       "(we may message the wrong people)")
    if points >= 4 and ev["map_accuracy"] >= 0.85:
        tier = "HIGH"
    elif points >= 2:
        tier = "MEDIUM"
    else:
        tier = "LOW"
    return tier, reasons


def lead_time(frames):
    first_high = next((f["minute"] for f in frames if score(f)[0] == "HIGH"), None)
    alarm = next((f["minute"] for f in frames if f["alarm_seen"]), None)
    return first_high, alarm


# ---------- 3. FALLBACK (works with no AI at all) ----------
def fallback(ev, tier, reasons):
    out = {
        "source": "TEMPLATE FALLBACK (AI explanation unavailable)",
        "summary": f"{tier} confidence incident near {ev['area']}. Evidence: " + "; ".join(reasons) + ".",
        "what_to_check": ["Confirm the cause with the field team or NOC tools",
                          "Check for a power outage in the area",
                          "Verify which customers sit behind the affected equipment"],
        "sms_en": None, "sms_fil": None,
    }
    if tier == "HIGH":
        out["sms_en"] = ("We are aware of a service problem in your area and our team is working "
                         "on it. We will send an update as soon as we have one.")
    return out


# ---------- 4. CLAUDE STEP (with guardrails enforced in code) ----------
def ask_claude(ev, tier, reasons, tickets):
    import anthropic
    client = anthropic.Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])
    prompt = f"""You help a telco NOC operator. Facts (computed by code, do not change them):
{json.dumps(ev)}
Confidence tier set by code: {tier}
Reasons: {reasons}
Sample customer tickets: {tickets}

Reply with ONLY a JSON object with these keys:
"summary": 2 plain sentences for the operator, saying what is known AND what is uncertain.
"what_to_check": list of 2-3 concrete checks that would confirm or rule out the cause.
"sms_en": a short customer SMS in English, or null.
"sms_fil": the same SMS in Filipino, or null.
Rules:
- If tier is not HIGH, "sms_en" and "sms_fil" MUST be null.
- Never state a repair time unless typical_repair_hours is not null; if it is null, give no time at all.
- Never claim a cause as certain unless the tier is HIGH."""
    msg = client.messages.create(model=MODEL, max_tokens=800,
                                 messages=[{"role": "user", "content": prompt}])
    text = msg.content[0].text
    data = json.loads(text[text.index("{"): text.rindex("}") + 1])
    if tier != "HIGH":
        data["sms_en"] = data["sms_fil"] = None
    if ev["typical_repair_hours"] is None:
        for key in ("sms_en", "sms_fil"):
            if data.get(key) and re.search(r"\d+\s*(hour|hr|oras|minute|min)", data[key], re.I):
                raise ValueError("Draft invented a time estimate")
    for key in ("summary", "what_to_check", "sms_en", "sms_fil"):
        data.setdefault(key, None)
    data["source"] = "Claude"
    return data


def analyse(ev, tier, reasons, tickets, simulate_outage=False):
    try:
        if simulate_outage:
            raise RuntimeError("Simulated AI outage (sidebar switch is on)")
        return ask_claude(ev, tier, reasons, tickets)
    except Exception as e:  # missing key, API down, bad JSON, failed guardrail
        res = fallback(ev, tier, reasons)
        res["error"] = str(e)[:200]
        return res


# ---------- 5. SCREEN ----------
def main():
    st.set_page_config(page_title="Outage Early-Warning (demo)")
    st.title("Outage Early-Warning: demo")
    st.warning("SIMULATED DATA. Nothing is actually sent to customers.")
    sim_fail = st.sidebar.checkbox("Simulate an AI outage (tests the fallback)")

    choice = st.selectbox("Pick a scenario", SCENARIOS)
    frames = FRAMES[choice]
    minute = st.select_slider("Replay: minutes since the first sign of trouble",
                              options=[f["minute"] for f in frames])
    ev = next(f for f in frames if f["minute"] == minute)
    tier, reasons = score(ev)
    pool = TICKET_POOL[choice[0]]
    tickets = random.Random(minute).sample(pool, min(3, len(pool))) if ev["tickets_last_15min"] else []

    colour = {"HIGH": "green", "MEDIUM": "orange", "LOW": "red"}[tier]
    st.markdown(f"### Minute {minute}: confidence :{colour}[{tier}]")
    st.caption(f"{ev['area']}  |  Event: {ev['event']}")
    st.write("**Evidence (counted by plain code):**")
    for r in reasons:
        st.write("- " + r)
    if tickets:
        st.write("**Sample tickets:**")
        for t in tickets:
            st.write(f"- {t}")

    key = f"res_{choice}_{minute}_{sim_fail}"
    if st.button("Analyse with AI"):
        st.session_state[key] = analyse(ev, tier, reasons, tickets, sim_fail)
    res = st.session_state.get(key)
    if res:
        st.divider()
        st.caption(f"Source: {res['source']}")
        if res.get("error"):
            st.info(f"AI step failed safely. Reason: {res['error']}")
        st.write(res["summary"])
        st.write("**What I'd check:**")
        for c in res["what_to_check"] or []:
            st.write("- " + c)
        if res["sms_en"]:
            st.write("**Draft SMS (English):** " + res["sms_en"])
            if res["sms_fil"]:
                st.write("**Draft SMS (Filipino, needs human review):** " + res["sms_fil"])
            if ev["typical_repair_hours"] is None:
                st.caption("No repair time stated: not enough history to estimate one honestly.")
            a, h = st.columns(2)
            if a.button("Approve (simulated send)"):
                st.success("Approved. In production this would send; here it is stubbed.")
            if h.button("Hold"):
                st.info("Held for the operator.")
        else:
            st.error("No customer message drafted: evidence is not strong enough. "
                     "A human should check the items above first.")

    st.divider()
    st.subheader("How confidence changed over this incident")
    first_high, alarm = lead_time(frames)
    c1, c2, c3 = st.columns(3)
    c1.metric("System first reaches HIGH", f"minute {first_high}" if first_high is not None else "never")
    c2.metric("NOC alarm appears", f"minute {alarm}" if alarm is not None else "never")
    if first_high is not None and alarm is not None:
        c3.metric("Head start (simulated)", f"{alarm - first_high} min")
    st.table([{"Minute": f["minute"], "Confidence": score(f)[0], "What happened": f["event"]}
              for f in frames])
    st.caption("All times and counts are invented for illustration. The head start compares the "
               "system with the moment a network alarm appears in this simulation, not with real NOC data.")


main()
