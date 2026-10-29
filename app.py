"""
Outage Early-Warning: STARTER DEMO (all data invented)

What this proves in miniature:
  1. Plain code (not the AI) counts the evidence and sets the confidence tier.
  2. Claude explains the evidence and drafts messages, but only when allowed.
  3. If Claude is unavailable or misbehaves, a plain template takes over.
  4. On thin evidence the system refuses to draft a customer message.
"""
import json
import random
import re

import streamlit as st

# Change this if your account says the model name is not found.
MODEL = "claude-sonnet-5-5"


# ---------- 1. INVENTED DATA (seeded, so it is identical every run) ----------
TICKET_POOL = [
    "Walang internet since 9:05, red light sa router",
    "Internet down, restarted twice, nothing",
    "No connection at all. Is the whole street affected?",
    "Wala pong signal, ilang minuto na",
    "Router blinking red, can't connect to anything",
    "Slow internet lately, maybe Wi-Fi problem?",
    "Intermittent connection, drops every few minutes",
    "Hindi makapag-work from home, down ang net",
]


def make_scenario(name):
    rnd = random.Random(42)
    if name == "A. Clean fibre cut":
        ev = dict(routers_total=180, routers_silent=171, alarm_seen=True,
                  tickets_last_15min=34, map_accuracy=0.95,
                  similar_past_incidents=12, typical_repair_hours=2.5,
                  area="Cabinet 14, Riverside")
        tickets = rnd.sample(TICKET_POOL[:5], 3)
    else:  # "B. Murky tower problem"
        ev = dict(routers_total=240, routers_silent=70, alarm_seen=False,
                  tickets_last_15min=9, map_accuracy=0.70,
                  similar_past_incidents=0, typical_repair_hours=None,
                  area="Tower 7, Hillside (fixed-wireless)")
        tickets = rnd.sample(TICKET_POOL[4:], 3)
    return ev, tickets


SCENARIOS = ["A. Clean fibre cut", "B. Murky tower problem"]


# ---------- 2. PLAIN-CODE RULES (the AI never sets the tier) ----------
def score(ev):
    silent_share = ev["routers_silent"] / ev["routers_total"]
    points, reasons = 0, []
    if silent_share >= 0.7:
        points += 2
        reasons.append(f"{silent_share:.0%} of routers silent (strong)")
    elif silent_share >= 0.25:
        points += 1
        reasons.append(f"{silent_share:.0%} of routers silent (partial; could be offline or just not reporting)")
    else:
        reasons.append(f"only {silent_share:.0%} of routers silent")
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
    if ev["map_accuracy"] >= 0.9:
        points += 1
        reasons.append(f"customer-to-equipment map {ev['map_accuracy']:.0%} accurate")
    else:
        reasons.append(f"customer-to-equipment map only {ev['map_accuracy']:.0%} accurate")
    if points >= 4 and ev["map_accuracy"] >= 0.85:
        tier = "HIGH"
    elif points >= 2:
        tier = "MEDIUM"
    else:
        tier = "LOW"
    return tier, reasons


# ---------- 3. FALLBACK (works with no AI at all) ----------
def fallback(ev, tier, reasons):
    out = {
        "source": "TEMPLATE FALLBACK (AI explanation unavailable)",
        "summary": f"{tier} confidence incident near {ev['area']}. Evidence: " + "; ".join(reasons) + ".",
        "what_to_check": ["Confirm cause with the field team or NOC tools",
                          "Check for a power outage in the area"],
        "sms_en": None, "sms_fil": None,
    }
    if tier == "HIGH":
        out["sms_en"] = ("We are aware of a service problem in your area and our team is working on it. "
                         "We will send an update as soon as we have one.")
    return out


# ---------- 4. CLAUDE STEP (with guardrails) ----------
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
    # Guardrails enforced in code, not by trusting the AI:
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


def analyse(ev, tier, reasons, tickets):
    try:
        return ask_claude(ev, tier, reasons, tickets)
    except Exception as e:  # missing key, API down, bad JSON, failed guardrail
        res = fallback(ev, tier, reasons)
        res["error"] = str(e)[:200]
        return res


# ---------- 5. SCREEN ----------
def main():
    st.set_page_config(page_title="Outage Early-Warning (demo)")
    st.title("Outage Early-Warning: starter demo")
    st.warning("SIMULATED DATA. Nothing is actually sent to customers.")

    choice = st.selectbox("Pick a scenario", SCENARIOS)
    ev, tickets = make_scenario(choice)
    tier, reasons = score(ev)

    colour = {"HIGH": "green", "MEDIUM": "orange", "LOW": "red"}[tier]
    st.markdown(f"### Confidence: :{colour}[{tier}]  |  {ev['area']}")
    st.write("**Evidence (counted by plain code):**")
    for r in reasons:
        st.write("- " + r)
    st.write("**Sample tickets:**")
    for t in tickets:
        st.write(f"- {t}")

    key = f"result_{choice}"
    if st.button("Analyse with AI"):
        st.session_state[key] = analyse(ev, tier, reasons, tickets)
    res = st.session_state.get(key)
    if not res:
        return

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
        a, h = st.columns(2)
        if a.button("Approve (simulated send)"):
            st.success("Approved. In production this would send; here it is stubbed.")
        if h.button("Hold"):
            st.info("Held for the operator.")
    else:
        st.error("No customer message drafted: evidence is not strong enough. "
                 "A human should check the items above first.")


main()
