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
    "C": ["Why was I charged twice this month? Please check my bill",
          "Sobra ang singil sa bill ko, dalawang beses ang charge",
          "My statement shows a duplicate charge, please fix",
          "Mali ang bill ko ngayong buwan, bakit may dagdag?",
          "Overcharged on my latest statement",
          "Internet is fine but my bill is wrong, charged for a service I cancelled"],
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

BASE_C = dict(routers_total=200, area="Metro East, many areas (not one cabinet)", map_accuracy=0.95,
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
    "C. Billing lookalike": [
        frame(0, 2, False, 3, "Normal operation, the usual billing questions", BASE_C),
        frame(10, 2, False, 18, "Complaints start rising after statements are emailed", BASE_C),
        frame(20, 3, False, 44, "Ticket spike across many areas; routers and alarms look healthy", BASE_C),
        frame(30, 3, False, 71, "Spike continues; the network is still clean", BASE_C),
        frame(45, 2, False, 52, "Billing team finds a duplicated-charge batch error", BASE_C),
    ],
}
SCENARIOS = list(FRAMES)


# ---------- 2. PLAIN-CODE RULES (the AI never sets the tier) ----------
BILLING_WORDS = ("bill", "charge", "statement", "singil", "bayad")


def billing_share(tickets):
    """Plain keyword check (the AI does this better, but this works with no AI)."""
    if not tickets:
        return 0.0
    hits = sum(any(w in t.lower() for w in BILLING_WORDS) for t in tickets)
    return hits / len(tickets)


def has_network_evidence(ev):
    return ev["routers_silent"] / ev["routers_total"] >= 0.25 or ev["alarm_seen"]


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
    if tier != "LOW" and not has_network_evidence(ev):
        tier = "LOW"
        reasons.append("RULE: a ticket spike alone is never enough; there is no network evidence "
                       "(few silent routers, no alarm), so confidence is capped at LOW")
    return tier, reasons


def lead_time(frames):
    first_high = next((f["minute"] for f in frames if score(f)[0] == "HIGH"), None)
    alarm = next((f["minute"] for f in frames if f["alarm_seen"]), None)
    return first_high, alarm


# ---------- 3. FALLBACK (works with no AI at all) ----------
def fallback(ev, tier, reasons, tickets=()):
    out = {
        "source": "TEMPLATE FALLBACK (AI explanation unavailable)",
        "summary": f"{tier} confidence incident near {ev['area']}. Evidence: " + "; ".join(reasons) + ".",
        "what_to_check": ["Confirm the cause with the field team or NOC tools",
                          "Check for a power outage in the area",
                          "Verify which customers sit behind the affected equipment"],
        "sms_en": None, "sms_fil": None,
    }
    if tickets and not has_network_evidence(ev) and billing_share(tickets) >= 0.6:
        out["route_to"] = "billing"
    else:
        out["route_to"] = "NOC" if tier != "LOW" else "none"
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
"route_to": one of "NOC", "billing", "care", "none".
Rules:
- If tier is not HIGH, "sms_en" and "sms_fil" MUST be null.
- Never state a repair time unless typical_repair_hours is not null; if it is null, give no time at all.
- Never claim a cause as certain unless the tier is HIGH.
- Use "billing" only if the sample tickets are mostly about bills or charges AND the facts show no network
  evidence (few silent routers, no alarm). In that case say plainly in the summary that this does not look
  like a network fault."""
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
    rt = data.get("route_to")
    if rt not in ("NOC", "billing", "care", "none"):
        rt = "NOC" if tier != "LOW" else "none"
    if rt == "billing" and has_network_evidence(ev):
        rt = "NOC"  # the AI may not talk us out of real network evidence
    data["route_to"] = rt
    data["source"] = "Claude"
    return data


def analyse(ev, tier, reasons, tickets, simulate_outage=False):
    try:
        if simulate_outage:
            raise RuntimeError("Simulated AI outage (sidebar switch is on)")
        return ask_claude(ev, tier, reasons, tickets)
    except Exception as e:  # missing key, API down, bad JSON, failed guardrail
        res = fallback(ev, tier, reasons, tickets)
        res["error"] = str(e)[:200]
        return res


# ---------- 4b. RESULTS MODEL (transparent arithmetic, invented inputs) ----------
DEFAULTS = dict(contact_pct=33, cost=2.50, defl_notified_pct=50, defl_today_pct=20,
                window=120, delay=3, ivr_delay=50, incidents_month=1500)


def _share_after(t, window):
    """Share of customer contacts that arrive after minute t (contacts spread evenly over the window)."""
    return max(0.0, window - t) / window


def incident_value(frames, p):
    first_high, alarm = lead_time(frames)
    if first_high is None or alarm is None:
        return None
    hi = next(f for f in frames if f["minute"] == first_high)
    contacts = hi["routers_total"] * p["contact_pct"] / 100
    sys_t = first_high + p["delay"]
    today_t = alarm + p["ivr_delay"]
    avoided_sys = (contacts * _share_after(sys_t, p["window"])
                   * p["defl_notified_pct"] / 100 * hi["map_accuracy"])
    avoided_today = contacts * _share_after(today_t, p["window"]) * p["defl_today_pct"] / 100
    net = avoided_sys - avoided_today
    per_incident = net * p["cost"]
    return dict(contacts=contacts, sys_t=sys_t, today_t=today_t, avoided_sys=avoided_sys,
                net=net, per_incident=per_incident,
                annual=per_incident * p["incidents_month"] * 12)


def early_broadcast(frames, p):
    """What if we had messaged customers at the very first sign instead of waiting for HIGH?"""
    r = incident_value(frames, p)
    first = next((f for f in frames if f["routers_silent"] > 0), None)
    if r is None or first is None:
        return None
    contacts = first["routers_total"] * p["contact_pct"] / 100
    early = (contacts * _share_after(first["minute"] + p["delay"], p["window"])
             * p["defl_notified_pct"] / 100 * first["map_accuracy"])
    extra = early - r["avoided_sys"]
    return dict(minute=first["minute"], extra_contacts=extra, extra_dollars=extra * p["cost"],
                wrongly_told=round(first["routers_total"] * (1 - first["map_accuracy"])))


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
        bs = billing_share(tickets)
        if bs >= 0.6 and not has_network_evidence(ev):
            st.info(f"Routing hint (keyword check): {bs:.0%} of the sample tickets mention bills or charges "
                    "and the network looks healthy. This is likely NOT a network fault: send it to billing "
                    "and do not send an outage message.")

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
        ROUTES = {"NOC": "Network operations", "billing": "Billing team (not a network fault)",
                  "care": "Customer care", "none": "No action yet; keep watching"}
        st.write("**Suggested routing:** " + ROUTES.get(res.get("route_to"), "No action yet; keep watching"))
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

    # ----- results panel -----
    st.divider()
    st.subheader("What this incident is worth (simulated)")
    p = dict(DEFAULTS)
    with st.expander("Assumptions behind these numbers (change them and watch the results move)"):
        a1, a2 = st.columns(2)
        p["contact_pct"] = a1.number_input("Affected customers who contact care (%)", 5, 100, DEFAULTS["contact_pct"])
        p["cost"] = a2.number_input("Cost per care contact ($)", 0.5, 20.0, DEFAULTS["cost"], 0.5)
        p["defl_notified_pct"] = a1.number_input("Contacts avoided when told before they call (%)", 0, 100, DEFAULTS["defl_notified_pct"])
        p["defl_today_pct"] = a2.number_input("Contacts avoided by today's generic phone message (%)", 0, 100, DEFAULTS["defl_today_pct"])
        p["window"] = a1.number_input("Minutes over which contacts arrive", 30, 480, DEFAULTS["window"])
        p["delay"] = a2.number_input("Minutes from HIGH to customer message (approval + delivery)", 0, 30, DEFAULTS["delay"])
        p["ivr_delay"] = a1.number_input("Minutes from NOC alarm to today's first customer information", 0, 240, DEFAULTS["ivr_delay"])
        p["incidents_month"] = a2.number_input("Network incidents per month", 100, 10000, DEFAULTS["incidents_month"], 100)

    r = incident_value(frames, p)
    if r is None:
        naive = next((f for f in frames if f["tickets_last_15min"] >= 20), None)
        if naive:
            st.warning(f"A detector that only watches complaint volume would have raised an outage alert at "
                       f"minute {naive['minute']} and told about {naive['routers_total']} customers there is a "
                       "network problem when there is none. This system held back and flagged it as a likely "
                       "billing issue. No savings are claimed here; the value is the wrong alert avoided.")
        else:
            st.info("This incident never reaches HIGH confidence in the replay, so no value is claimed.")
    else:
        m1, m2, m3 = st.columns(3)
        m1.metric("Contacts expected", f"{r['contacts']:.0f}")
        m2.metric("Contacts avoided (net)", f"{r['net']:.0f}", f"{r['net'] / r['contacts']:.0%} of expected", delta_color="off")
        m3.metric("Care cost saved", f"${r['per_incident']:.0f}")
        st.write(f"The system would message customers around **minute {r['sys_t']}**. Today, the first "
                 f"useful customer information arrives around **minute {r['today_t']}**. "
                 f"Contacts are assumed to arrive evenly over {p['window']} minutes, so every minute "
                 "earlier removes a slice of them.")

    st.write("**Both scenarios side by side**")
    rows = []
    for name, fr in FRAMES.items():
        v = incident_value(fr, p)
        if v:
            rows.append({"Scenario": name, "Message at (min)": v["sys_t"],
                         "Today's first info at (min)": v["today_t"],
                         "Contacts avoided": round(v["net"], 1),
                         "Saved per incident": f"${v['per_incident']:.0f}",
                         "If EVERY incident looked like this, per year": f"${v['annual']:,.0f}"})
    st.table(rows)
    st.caption("Reference: the Part 1 model assumed about 35% of outage contacts avoided, about $0.95M a year "
               "gross, for 1,500 incidents a month. Scenario A lands close to that because this timeline "
               "model was built to be consistent with it. That is a consistency check, not independent "
               "evidence. Scenario B is lower because its map is less accurate and the evidence arrives later. "
               "Running costs (SMS, AI) are not subtracted here.")

    eb = early_broadcast(frames, p)
    if eb:
        st.write("**Why not message at the first sign of trouble?**")
        e1, e2 = st.columns(2)
        e1.metric(f"Extra contacts avoided by messaging at minute {eb['minute']}",
                  f"{eb['extra_contacts']:.1f}", f"about ${eb['extra_dollars']:.0f}", delta_color="off")
        e2.metric("Customers possibly told wrongly at that moment", f"{eb['wrongly_told']}")
        st.caption("Waiting for stronger evidence costs little in this model, while a wrong message goes to "
                   "people who are not affected. This ignores the chance the first sign is a false alarm, "
                   "which would make early messaging look worse. The right threshold must be set from real "
                   "incident history.")


main()
