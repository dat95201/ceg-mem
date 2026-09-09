"""Build every figure in paper/draft/figures/ from numbers.json + figdata.json.

Print conventions this file commits to, once, so no figure drifts:

  * ONE y-axis per panel. Two quantities of different scale become two panels
    (small multiples), never a twin axis.
  * The no-memory arm is neutral gray: it is the reference the others are read
    against, not a competing series. The four treatment arms take a fixed hue
    order that passes the categorical checks all-pairs (blue, orange-red,
    green, magenta), and every one of them also carries a distinct marker and
    dash pattern, so the figures survive grayscale printing and the 6-8 CVD
    band the palette sits in for the green/orange pair.
  * Direct labels on the series that matter, at the right edge; a legend only
    where direct labels would collide.
  * Recessive axes: no top/right spine, hairline y-grid, no x-grid.
  * Column width 3.33in (ACM two-column), full width 7.0in.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

# Matplotlib stamps /CreationDate into every PDF; pin it (2026-09-01T00:00Z, the
# reported run's date) so a reproduction rewrites byte-identical figures instead
# of dirtying the tree. The caller's own SOURCE_DATE_EPOCH wins if set.
os.environ.setdefault("SOURCE_DATE_EPOCH", "1788220800")

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator, PercentFormatter

HERE = pathlib.Path(__file__).resolve().parent
# Usage:  python tools/make_figures.py [DRAFT_DIR]
# DRAFT_DIR holds numbers.json and figdata.json and receives figures/.
# It defaults to this file's parent directory, or its grandparent when this
# file sits in the conventional paper/draft/tools/ location.
DRAFT = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else (
    HERE.parent if HERE.name == "tools" else HERE)
NUM = json.loads((DRAFT / "numbers.json").read_text())
FIG = json.loads((DRAFT / "figdata.json").read_text())
OUT = DRAFT / "figures"
OUT.mkdir(parents=True, exist_ok=True)

INK = "#1a1a1a"
MUTED = "#5c5c5c"
HAIR = "#d8d8d4"
REF = "#6b6b6b"          # the no-memory reference
BLUE = "#2F6FE0"
RED = "#C1553B"
GREEN = "#2F8F6B"
MAGENTA = "#A64BA6"

ARM = {
    "no_memory":  dict(color=REF,     ls=(0, (5, 2)), marker="",  label="no memory"),
    "untyped":    dict(color=BLUE,    ls="-",         marker="o", label="untyped"),
    "typed":      dict(color=RED,     ls="-",         marker="s", label="typed"),
    "guard_only": dict(color=GREEN,   ls=(0, (4, 1.5, 1, 1.5)), marker="^", label="guard-only"),
    "steer_only": dict(color=MAGENTA, ls=(0, (1, 1.6)), marker="D", label="steer-only"),
}

plt.rcParams.update({
    "font.family": "serif",
    # Liberation Serif is metric-compatible with Times (the paper's body font)
    # and is a TrueType file, so fonttype 42 embeds it without the Type1/CFF
    # wrapper mismatch that OTF clones (TeX Gyre Termes) produce - ACM and
    # IEEE preflight both flag that. Type 3 is forbidden, hence fonttype 42.
    "font.serif": ["Liberation Serif", "Tinos", "Times New Roman",
                   "TeX Gyre Termes", "DejaVu Serif"],
    "mathtext.fontset": "stix",
    "font.size": 8,
    "axes.labelsize": 8,
    "axes.titlesize": 8.5,
    "legend.fontsize": 7.2,
    "xtick.labelsize": 7.4,
    "ytick.labelsize": 7.4,
    "axes.edgecolor": MUTED,
    "axes.labelcolor": INK,
    "text.color": INK,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "axes.linewidth": 0.6,
    "xtick.major.width": 0.6,
    "ytick.major.width": 0.6,
    "lines.linewidth": 1.3,
    "lines.markersize": 3.4,
    "legend.frameon": True,
    "legend.facecolor": "white",
    "legend.edgecolor": "white",
    "legend.framealpha": 0.92,
    "legend.fancybox": False,
    "legend.borderpad": 0.3,
    "figure.dpi": 200,
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.015,
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
})


def tidy(ax, *, ygrid=True):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    if ygrid:
        ax.yaxis.grid(True, color=HAIR, lw=0.5)
        ax.set_axisbelow(True)


def save(fig, name):
    p = OUT / name
    fig.savefig(p)
    plt.close(fig)
    print(f"  wrote {p.name}")


# ── Figure 1: success at a matched ORACLE budget - the primary metric ───────
def fig_budget_curve():
    c = NUM["success_at_oracle_budget"]
    fig, ax = plt.subplots(figsize=(3.33, 2.42))
    for name in ["no_memory", "steer_only", "untyped", "typed", "guard_only"]:
        d = c[name]
        xs = list(range(1, 21))
        ys = [d[str(b)] for b in xs]
        st = ARM[name]
        ax.plot(xs, ys, color=st["color"], ls=st["ls"], marker=st["marker"],
                markevery=(0, 3), markeredgewidth=0, label=st["label"],
                lw=1.5 if name in ("typed", "no_memory") else 1.15,
                zorder=4 if name == "typed" else 3)
    # the headline gap, called out with a leader rather than crowded text
    lo, hi = c["no_memory"]["2"], c["typed"]["2"]
    ax.plot([2, 2], [lo, hi], color=RED, lw=0.9, solid_capstyle="butt", zorder=5)
    for y in (lo, hi):
        ax.plot([2], [y], marker="_", ms=5, color=RED, mew=1.1, zorder=5)
    ax.annotate(f"$+${100 * (hi - lo):.0f} pp at $b{{=}}2$", xy=(2.15, (lo + hi) / 2),
                xytext=(5.2, 0.30), fontsize=7, color=RED,
                arrowprops=dict(arrowstyle="-", color=RED, lw=0.6,
                                shrinkA=1, shrinkB=2))
    ax.set_xlim(0.7, 20.6)
    ax.set_ylim(0, 0.78)
    ax.set_xticks([1, 2, 4, 6, 8, 12, 16, 20])
    ax.yaxis.set_major_formatter(PercentFormatter(xmax=1, decimals=0))
    ax.set_xlabel("oracle-call budget $b$")
    ax.set_ylabel("repaired within $b$ oracle calls")
    ax.legend(loc="lower right", ncol=1, handlelength=2.4,
              borderaxespad=0.2, labelspacing=0.28)
    tidy(ax)
    save(fig, "fig-budget-curve.pdf")


# ── Figure 2: the same budget, counted in proposals - the tautology guard ──
def fig_budget_proposals():
    co = NUM["success_at_oracle_budget"]
    cp = NUM["success_at_proposal_budget"]
    fig, axes = plt.subplots(1, 2, figsize=(7.0, 2.15), sharey=True)
    for ax, src, title in ((axes[0], co, "(a) budget in oracle calls"),
                           (axes[1], cp, "(b) budget in proposals")):
        for name in ("no_memory", "untyped", "typed"):
            xs = list(range(1, 21))
            ys = [src[name][str(b)] for b in xs]
            st = ARM[name]
            ax.plot(xs, ys, color=st["color"], ls=st["ls"], marker=st["marker"],
                    markevery=(0, 3), markeredgewidth=0, label=st["label"])
        ax.set_title(title, loc="left", color=INK)
        ax.set_xlabel("budget $b$")
        ax.set_xticks([1, 4, 8, 12, 16, 20])
        ax.set_xlim(0.7, 20.5)
        ax.set_ylim(0, 0.80)
        tidy(ax)
    axes[0].yaxis.set_major_formatter(PercentFormatter(xmax=1, decimals=0))
    axes[0].set_ylabel("repaired within $b$")
    axes[1].legend(loc="lower right", ncol=1)
    save(fig, "fig-budget-both.pdf")


# ── Figure 3: oracle cost per band, and where the saving lives ──────────────
def fig_per_band():
    pb = NUM["per_band"]
    bands = ["dead", "hard", "medium", "easy", "too_easy"]
    pretty = {"dead": "dead", "hard": "hard", "medium": "medium",
              "easy": "easy", "too_easy": "too-easy"}
    fig, axes = plt.subplots(1, 2, figsize=(7.0, 2.2))
    ax = axes[0]
    w = 0.26
    xs = range(len(bands))
    for i, name in enumerate(("no_memory", "untyped", "typed")):
        vals = [pb[b][name]["oracle_calls"] for b in bands]
        ax.bar([x + (i - 1) * w for x in xs], vals, width=w * 0.92,
               color=ARM[name]["color"], edgecolor="white", linewidth=0.6,
               label=ARM[name]["label"], zorder=3)
    for x, b in zip(xs, bands):
        ax.annotate(f"$\\times${pb[b]['x_typed_vs_nomem_oracle']:.1f}",
                    xy=(x, pb[b]["no_memory"]["oracle_calls"] + 0.7),
                    ha="center", fontsize=6.6, color=RED)
    ax.set_xticks(list(xs))
    ax.set_xticklabels([pretty[b] for b in bands])
    ax.set_ylim(0, 23)
    ax.set_ylabel("oracle calls / episode")
    ax.set_xlabel("difficulty band (from E1's $\\hat\\pi$)")
    ax.set_title("(a) verification cost", loc="left", color=INK)
    ax.legend(loc="upper right")
    tidy(ax)

    ax = axes[1]
    for i, name in enumerate(("no_memory", "untyped", "typed")):
        vals = [pb[b][name]["redundancy_paid"] for b in bands]
        ax.bar([x + (i - 1) * w for x in xs], vals, width=w * 0.92,
               color=ARM[name]["color"], edgecolor="white", linewidth=0.6,
               zorder=3)
    ax.set_xticks(list(xs))
    ax.set_xticklabels([pretty[b] for b in bands])
    for x, b in zip(xs, bands):
        for i, name in enumerate(("untyped", "typed")):
            v = pb[b][name]["redundancy_paid"]
            ax.annotate(f"{v:.2f}", xy=(x + i * w, 0.35), rotation=90,
                        ha="center", va="bottom", fontsize=6.1,
                        color=ARM[name]["color"])
    ax.set_ylabel("redundant oracle calls / episode")
    ax.set_xlabel("difficulty band")
    ax.set_title("(b) of which re-verify a known refutation", loc="left", color=INK)
    ax.set_ylim(0, 12)
    tidy(ax)
    save(fig, "fig-per-band.pdf")


# ── Figure 4: guard cost on identical episodes (the clean separation) ──────
def fig_guard_cost():
    d = FIG["guard_sec_paired"]
    pairs = [(u, g) for u, g in zip(d["untyped"], d["guard_only"]) if u > 0 and g > 0]
    gs = NUM["e3_guard_only"]["guard_sec_sign"]
    fig, ax = plt.subplots(figsize=(3.33, 2.45))
    ax.scatter([p[0] for p in pairs], [p[1] for p in pairs], s=7, color=GREEN,
               alpha=0.6, edgecolors="none", zorder=3)
    lim = (0.04, 420)
    ax.plot(lim, lim, color=MUTED, lw=0.7, ls=(0, (4, 2)), zorder=2)
    ax.annotate("equal cost", xy=(6.0, 10.5), fontsize=6.6, color=MUTED, rotation=40)
    ax.annotate(f"typed index cheaper on\n{gs['wins']} of {gs['wins'] + gs['losses']} "
                f"episodes where\nthe guard ran ($p{{<}}10^{{-24}}$)",
                xy=(0.7, 0.075), fontsize=6.8, color=GREEN)
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_xlim(*lim); ax.set_ylim(*lim)
    ax.set_xlabel("untyped: flat scan (s)")
    ax.set_ylabel("guard-only: typed index (s)")
    tidy(ax, ygrid=False)
    save(fig, "fig-guard-cost.pdf")


# ── Figure 5: typing noise - the dose-response and its null ────────────────
def fig_typing_dose():
    e5 = NUM["e5_typing_noise"]
    bi = NUM["bucket_index"]["sweep_by_level"]
    ct = NUM["e5_control_tests"]["tests"]["random"]["redundancy_caught"]
    cs = [1.0, 0.9, 0.75, 0.5, 0.25, 0.0]
    fig, axes = plt.subplots(1, 2, figsize=(7.0, 2.1))

    # (a) the column that responds to typing quality
    ax = axes[0]
    ys = [e5[str(c)]["redundancy_caught"] for c in cs]
    rnd = e5["random"]["redundancy_caught"]
    ax.plot(cs, ys, color=RED, marker="s", markeredgewidth=0, zorder=4,
            label="typed, coherence $c$")
    ax.axhline(rnd, color=MAGENTA, lw=1.2, ls=(0, (1, 1.6)), zorder=3,
               label="random partition (control)")
    ax.annotate(f"$c{{=}}1$ catches more on\n{ct['wins']} of {ct['wins'] + ct['losses']} "
                f"discordant cells\n($p{{=}}{ct['p']:.3f}$)",
                xy=(0.62, 1.15), fontsize=6.7, color=RED)
    ax.set_xlim(1.07, -0.07); ax.set_ylim(0, 6.0)
    ax.set_xticks([1.0, 0.75, 0.5, 0.25, 0.0])
    ax.set_xlabel("typing coherence $c$")
    ax.set_ylabel("redundant proposals caught / ep.")
    ax.set_title("(a) recognition: responds to $c$", loc="left", color=INK)
    ax.legend(loc="upper left", labelspacing=0.25, borderaxespad=0.2)
    tidy(ax)

    # (b) the column that does not - shown so the (a) claim is not overread
    ax = axes[1]
    ys = [bi[str(c)]["hit_rate"] for c in cs]
    ax.plot(cs, ys, color=RED, marker="s", markeredgewidth=0, zorder=4)
    ax.axhline(bi["random"]["hit_rate"], color=MAGENTA, lw=1.2,
               ls=(0, (1, 1.6)), zorder=3)
    ax.annotate("random partition", xy=(0.60, bi["random"]["hit_rate"] + 0.045),
                fontsize=6.7, color=MAGENTA)
    ax.annotate("no dose-response:\nan uninformative partition\n"
                "still fills buckets", xy=(0.62, 0.055), fontsize=6.7, color=MUTED)
    ax.set_xlim(1.07, -0.07); ax.set_ylim(0, 0.45)
    ax.set_xticks([1.0, 0.75, 0.5, 0.25, 0.0])
    ax.yaxis.set_major_formatter(PercentFormatter(xmax=1, decimals=0))
    ax.set_xlabel("typing coherence $c$")
    ax.set_ylabel("$O(1)$ index-hit rate")
    ax.set_title("(b) index hits: flat in $c$", loc="left", color=INK)
    tidy(ax)
    save(fig, "fig-typing-dose.pdf")


# ── Figure 6: oracle depth - acceptance is not repair ──────────────────────
def fig_oracle_depth():
    e4 = NUM["e4_oracle_depth"]
    ks = [3, 8, 20, 100]
    fig, ax = plt.subplots(figsize=(3.33, 2.25))
    acc = [e4[str(k)]["accepted_rate"] for k in ks]
    tru = [e4[str(k)]["true_success"] for k in ks]
    ovf = [e4[str(k)]["overfit_rate"] for k in ks]
    ax.plot(ks, acc, color=BLUE, marker="o", markeredgewidth=0, ls=(0, (5, 2)),
            label="accepted by the oracle", zorder=3)
    ax.plot(ks, tru, color=RED, marker="s", markeredgewidth=0,
            label="passes the whole pool (audited)", zorder=4)
    ax.plot(ks, ovf, color=MAGENTA, marker="D", markeredgewidth=0, ls=(0, (1, 1.6)),
            label="overfitting among accepts", zorder=3)
    ax.fill_between(ks, tru, acc, color=BLUE, alpha=0.10, zorder=2)
    ax.annotate("accepted but\nnot pool-adequate", xy=(3.35, 0.655), fontsize=6.8,
                color=BLUE)
    ax.annotate("saturates at $k{\\approx}20$", xy=(13, 0.585), fontsize=6.8, color=RED)
    ax.set_xscale("log")
    ax.set_xticks(ks)
    ax.set_xticklabels([str(k) for k in ks])
    ax.set_xlabel("oracle depth $k$ (test cases per verification)")
    ax.set_ylabel("rate")
    ax.set_ylim(0, 1.0)
    ax.yaxis.set_major_formatter(PercentFormatter(xmax=1, decimals=0))
    ax.legend(loc="lower right", bbox_to_anchor=(1.0, 0.10),
              labelspacing=0.26, borderaxespad=0.2)
    tidy(ax)
    save(fig, "fig-oracle-depth.pdf")


# ── Figure 7: what steering costs - prompt growth per round ───────────────
def fig_prompt_growth():
    ct = FIG["context_tokens_by_round"]
    fig, ax = plt.subplots(figsize=(3.33, 2.15))
    for name in ("typed", "untyped", "no_memory"):
        d = ct[name]
        ys = d["mean_by_round"]
        xs = list(range(1, len(ys) + 1))
        st = ARM[name]
        ax.plot(xs, ys, color=st["color"], ls=st["ls"], marker=st["marker"],
                markevery=(0, 4), markeredgewidth=0, zorder=3,
                # U+2212, not a hyphen: a negative slope in a legend is a
                # number, and a hyphen there reads as a dash.
                label=f"{st['label']}  ({d['slope']:+.1f}".replace("-", "\u2212")
                      + " tok/round)")
    ax.annotate("first evidence block\nenters the prompt", xy=(2, 692),
                xytext=(2.6, 505), fontsize=6.8, color=RED,
                arrowprops=dict(arrowstyle="->", color=RED, lw=0.6,
                                shrinkA=1, shrinkB=2))
    ax.set_xlim(0.6, 20.4)
    ax.set_ylim(420, 830)
    ax.set_xticks([1, 5, 10, 15, 20])
    ax.set_xlabel("round index")
    ax.set_ylabel("prompt tokens (mean)")
    ax.legend(loc="center right", bbox_to_anchor=(1.0, 0.62),
              labelspacing=0.26, borderaxespad=0.4)
    tidy(ax)
    save(fig, "fig-prompt-growth.pdf")


# ── Figure 8: where each arm's episode time goes ───────────────────────────
def fig_wall_clock():
    w = NUM["wall_clock_five_arms"]
    order = ["guard_only", "untyped", "steer_only", "no_memory", "typed"]
    fig, ax = plt.subplots(figsize=(3.33, 1.85))
    ys = range(len(order))
    vals = [w[k] for k in order]
    ax.barh(list(ys), vals, height=0.62,
            color=[ARM[k]["color"] for k in order],
            edgecolor="white", linewidth=0.6, zorder=3)
    for y, v in zip(ys, vals):
        ax.annotate(f"{v:.0f}s", xy=(v + 2.5, y), va="center", fontsize=7,
                    color=INK)
    ax.set_yticks(list(ys))
    ax.set_yticklabels([ARM[k]["label"] for k in order])
    ax.invert_yaxis()
    ax.set_xlim(0, 178)
    ax.set_xlabel("wall-clock seconds per episode")
    ax.xaxis.grid(True, color=HAIR, lw=0.5)
    ax.set_axisbelow(True)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_visible(False)
    ax.tick_params(axis="y", length=0)
    save(fig, "fig-wall-clock.pdf")


if __name__ == "__main__":
    print("figures ->", OUT)
    fig_budget_curve()
    fig_budget_proposals()
    fig_per_band()
    fig_guard_cost()
    fig_typing_dose()
    fig_oracle_depth()
    fig_prompt_growth()
    fig_wall_clock()
    print("done")
