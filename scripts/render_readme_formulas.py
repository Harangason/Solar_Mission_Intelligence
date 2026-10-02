"""Render the README's formula summaries using the existing Matplotlib dependency.

This is documentation tooling, not a second calculation engine. Equations must
be kept consistent with README.md and the referenced solver implementations.
No TeX installation, network access, or external fonts are required.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from html import escape
from io import StringIO
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WIDTH = 900
HEADER = 96
ROW_HEIGHT = 84
FOOTER = 72


@dataclass(frozen=True)
class FormulaCard:
    title: str
    subtitle: str
    rows: tuple[tuple[str, str], ...]
    note: str
    description: str


CARDS = {
    "state-dynamics-rk4": FormulaCard(
        "Bewegungsgleichung und RK4",
        "Zustand aus Ort und Geschwindigkeit · RK4 in der separaten Missionssimulation",
        (
            ("Zustandsableitung", r"\dot{\mathbf{r}}=\mathbf{v},\qquad\dot{\mathbf{v}}=\mathbf{a},\qquad\dot{\mathbf{x}}=\mathbf{f}(t,\mathbf{x})"),
            ("Zwei-Körper-Beschleunigung", r"\mathbf{a}=-\mu\frac{\mathbf{r}}{r^3},\qquad r=\Vert\mathbf{r}\Vert>0"),
            ("Erste Steigung", r"\mathbf{k}_1=\mathbf{f}(t_n,\mathbf{x}_n)"),
            ("Zweite Steigung", r"\mathbf{k}_2=\mathbf{f}(t_n+h/2,\mathbf{x}_n+h\mathbf{k}_1/2)"),
            ("Dritte Steigung", r"\mathbf{k}_3=\mathbf{f}(t_n+h/2,\mathbf{x}_n+h\mathbf{k}_2/2)"),
            ("Vierte Steigung", r"\mathbf{k}_4=\mathbf{f}(t_n+h,\mathbf{x}_n+h\mathbf{k}_3)"),
            ("Gewichteter Schritt", r"\mathbf{x}_{n+1}=\mathbf{x}_n+\frac{h}{6}(\mathbf{k}_1+2\mathbf{k}_2+2\mathbf{k}_3+\mathbf{k}_4)"),
        ),
        "h = Δt > 0. Bei glatter rechter Seite: lokal O(h⁵), global O(h⁴).\nRK4 enthält hier keine eingebettete Fehlerschätzung; der Routenplanner verwendet DOP853.",
        "Ort wird aus der Geschwindigkeit, Geschwindigkeit aus der Beschleunigung integriert. "
        "Vier RK4-Steigungen ergeben den gewichteten nächsten Zustand. Die vierte Steigung "
        "verwendet den Zustand x_n plus h mal k_3.",
    ),
    "kepler-anomalies": FormulaCard(
        "Keplerellipse als Ersatzephemeride",
        "Elliptisches Modell · a > 0 und 0 ≤ e < 1 · Winkel im Bogenmaß",
        (
            ("Winkel aus den Bahnelementen", r"M=L-\varpi,\qquad\omega=\varpi-\Omega"),
            ("Keplergleichung", r"E-e\sin E=M"),
            ("Newton-Iteration", r"E_{k+1}=E_k-\frac{E_k-e\sin E_k-M}{1-e\cos E_k}"),
            ("Wahre Anomalie mit korrektem Quadranten", r"\nu=\operatorname{atan2}(\sqrt{1-e^2}\sin E,\,\cos E-e)"),
            ("Bahnradius", r"r=a(1-e\cos E)=\frac{a(1-e^2)}{1+e\cos\nu}"),
            ("Position in der Bahnebene", r"x'=a(\cos E-e),\qquad y'=a\sqrt{1-e^2}\sin E,\qquad z'=0"),
            ("Rotation in das ekliptikale System", r"\mathbf{r}=R_z(\Omega)R_x(i)R_z(\omega)\mathbf{r}'"),
        ),
        "Die kartesische E-Auswertung vermeidet den Tangens-Pol bei E = π.\nParabeln und Hyperbeln benötigen andere Parametrisierungen.",
        "Die exzentrische Anomalie löst die Keplergleichung durch Newton-Iteration. "
        "atan2 liefert die wahre Anomalie mit korrektem Quadranten. Die Position "
        "in der Bahnebene wird in das ekliptikale System rotiert.",
    ),
    "solar-oberth": FormulaCard(
        "Solar-Oberth-Manöver",
        "Ideale Sundiver-Ellipse · prograder Impuls · spezifische Energie in km²/s²",
        (
            ("Perihel und Transferhalbachse", r"r_{\mathrm{p}}=q\,\mathrm{AU},\qquad a_{\mathrm{t}}=\frac{r_0+r_{\mathrm{p}}}{2}"),
            ("Vis-Viva-Gleichung", r"v(r)=\sqrt{\mu_\odot\left(\frac{2}{r}-\frac{1}{a_{\mathrm{t}}}\right)}"),
            ("Prograder Impuls", r"\mathbf{v}^+=\mathbf{v}^-+\Delta v_{\mathrm{O}}\frac{\mathbf{v}^-}{\Vert\mathbf{v}^-\Vert}"),
            ("Spezifischer Energiegewinn", r"\Delta\varepsilon=v^-\Delta v_{\mathrm{O}}+\frac{(\Delta v_{\mathrm{O}})^2}{2}"),
            ("Eingestrahlter Solarfluss", r"S(r)=S_0\left(\frac{\mathrm{AU}}{r}\right)^2,\qquad S_0=1361\,\mathrm{W\,m^{-2}}"),
            ("Beispiel: q = 0.05", r"S(0.05\,\mathrm{AU})=544400\,\mathrm{W\,m^{-2}}"),
        ),
        "q ist dimensionslos; r₀ ist das Aphel dieser idealen Transferellipse.\nSolarfluss ist keine Temperatur. Das Perihel muss außerhalb der Sonne liegen.",
        "Die Transferhalbachse ist der Mittelwert von Startabstand und Perihel. "
        "Ein prograder Oberth-Impuls erhöht die spezifische Energie. Der Solarfluss "
        "steigt mit dem inversen Quadrat des Sonnenabstands.",
    ),
    "rocket-equation": FormulaCard(
        "Impulse und Treibstoffbilanz",
        "Konstanter spezifischer Impuls · m₀ ≥ m_f > 0 · I_sp > 0",
        (
            ("Einheiten für den km/s-Solver", r"g_0=9.80665\,\mathrm{m\,s^{-2}}=0.00980665\,\mathrm{km\,s^{-2}}"),
            ("Raketengleichung", r"\Delta v=I_{\mathrm{sp}}g_0\ln\left(\frac{m_0}{m_f}\right)"),
            ("Masse nach dem Impuls", r"m_f=m_0\exp\left(-\frac{\Delta v}{I_{\mathrm{sp}}g_0}\right)"),
            ("Benötigte Treibstoffmasse", r"m_{\mathrm{prop}}=m_0-m_f=m_0\left[1-\exp\left(-\frac{\Delta v}{I_{\mathrm{sp}}g_0}\right)\right]"),
            ("Budget aus einzelnen Impulsbeträgen", r"\Delta v_{\mathrm{gesamt}}=\sum_j\Vert\Delta\mathbf{v}_j\Vert"),
        ),
        "Δv und I_sp · g₀ müssen dieselbe Geschwindigkeitseinheit besitzen.\nTreibstoffbedarf und verfügbare Masse werden für jedes Manöver getrennt geprüft.",
        "Die Raketengleichung verknüpft Delta-v, spezifischen Impuls und Massenverhältnis. "
        "Bei Delta-v in Kilometer pro Sekunde beträgt g_0 0.00980665 Kilometer pro Sekunde "
        "zum Quadrat. Das Gesamtbudget summiert die Beträge aller Einzelimpulse.",
    ),
    "continuous-propulsion": FormulaCard(
        "Kontinuierliche Antriebe",
        "Separate Missionssimulation · T in N, m in kg · ρ = r / AU",
        (
            ("Schubbeschleunigung in SI-Einheiten", r"\mathbf{a}_{\mathrm{prop}}=\frac{T}{m}\hat{\mathbf{d}},\qquad 1\,\mathrm{m\,s^{-2}}=10^{-3}\,\mathrm{km\,s^{-2}}"),
            ("Positiver Verbrauch, negative Massenänderung", r"\dot m_{\mathrm{prop,verbrauch}}=\frac{T}{I_{\mathrm{sp}}g_0},\qquad\dot m=-\dot m_{\mathrm{prop,verbrauch}}"),
            ("Solarsegel: Modell mit begrenztem Nenner", r"T_{\mathrm{SS}}=\frac{p_0 A_{\mathrm{s}}C_R}{\max(\rho^2,0.01)},\qquad p_0=9.08\cdot10^{-6}\,\mathrm{N\,m^{-2}}"),
            ("Electric Sail: empirische Referenzkraft", r"T_{\mathrm{ES}}=(1\,\mathrm{N})\,\frac{NL}{2000\,\mathrm{km}}\,\frac{U}{20\,\mathrm{kV}}\,\frac{\eta_{\mathrm{sw}}}{\max(\rho,0.1)}"),
        ),
        "Beide Schubmodelle begrenzen den Nenner unterhalb von 0.1 AU.\nDie Modellklammer ersetzt keine reale Nahsonnenphysik oder Niedrigschub-Optimierung.",
        "Schub geteilt durch Masse ergibt SI-Beschleunigung. Der Solver konvertiert "
        "in Kilometer pro Sekunde zum Quadrat. Der Verbrauch ist positiv, die "
        "Massenänderung negativ. Solar- und Electric-Sail-Nenner sind begrenzt.",
    ),
    "lambert-universal-variable": FormulaCard(
        "Lambert-Randwerttransfer",
        "Zwei Positionen und Δt > 0 · r₁, r₂ > 0 · nicht kollineare Randvektoren",
        (
            ("Transfergeometrie und Seite", r"c=\frac{\mathbf{r}_1\cdot\mathbf{r}_2}{r_1r_2},\qquad\sin\theta=\sigma\sqrt{1-c^2},\qquad\sigma\in\{-1,+1\}"),
            ("Geometriefaktor mit Einheit km", r"A=\sin\theta\sqrt{\frac{r_1r_2}{1-c}}=\sigma\sqrt{r_1r_2(1+c)}"),
            ("Stumpff-Grenzwerte", r"C(0)=\frac{1}{2},\qquad S(0)=\frac{1}{6}"),
            ("Hilfsgröße mit Einheit km", r"y(z)=r_1+r_2+A\frac{zS(z)-1}{\sqrt{C(z)}}"),
            ("Zeitgleichung für die universelle Variable", r"F(z)=\left(\frac{y}{C}\right)^{3/2}S+A\sqrt{y}-\sqrt{\mu}\,\Delta t=0"),
            ("Lagrange-Koeffizienten; g in Sekunden", r"f=1-\frac{y}{r_1},\qquad g=A\sqrt{\frac{y}{\mu}},\qquad\dot g=1-\frac{y}{r_2}"),
            ("Abflug- und Ankunftsgeschwindigkeit", r"\mathbf{v}_1=\frac{\mathbf{r}_2-f\mathbf{r}_1}{g},\qquad\mathbf{v}_2=\frac{\dot g\mathbf{r}_2-\mathbf{r}_1}{g}"),
        ),
        "C(z) > 0, y(z) > 0 und g ≠ 0. Vollständige Stumpff-Definitionen stehen in der README.\nDie begrenzte Kandidatensuche garantiert kein globales Optimum.",
        "Der Lambert-Geometriefaktor A ist sin theta mal der Wurzel aus r_1 r_2 "
        "geteilt durch 1 minus cos theta. Die vereinfachte Form enthält keinen "
        "zusätzlichen Nenner. Eine Nullstelle der Zeitgleichung liefert über die "
        "Lagrange-Koeffizienten beide Randgeschwindigkeiten.",
    ),
    "patched-conics-swingby": FormulaCard(
        "Patched Conics und Swing-by",
        "Lokale Zwei-Körper-Hyperbel · positive Überschussenergie · passiver Vorbeiflug",
        (
            ("Laplace-Einflusssphäre des Planeten", r"r_{\mathrm{SOI}}=a_p\left(\frac{m_p}{M_\odot}\right)^{2/5}"),
            ("Relativgeschwindigkeit am endlichen Abstand d", r"\mathbf{u}=\mathbf{v}-\mathbf{v}_p,\qquad v_\infty=\sqrt{\Vert\mathbf{u}\Vert^2-\frac{2\mu_p}{d}}"),
            ("Hyperbelexzentrizität und Ablenkung", r"e_h=1+\frac{r_{\mathrm{p}}v_\infty^2}{\mu_p},\qquad\delta=2\arcsin\left(\frac{1}{e_h}\right)"),
            ("Perizentrum aus dem Ablenkwinkel", r"r_{\mathrm{p}}=\frac{\mu_p}{v_\infty^2}\left[\frac{1}{\sin(\delta/2)}-1\right]"),
            ("Maximale sichere Ablenkung", r"\delta_{\max}=2\arcsin\left(\frac{1}{1+(R_p+h_{\min})v_\infty^2/\mu_p}\right)"),
            ("Erhaltung des asymptotischen Betrags", r"\Vert\mathbf{v}_\infty^+\Vert=\Vert\mathbf{v}_\infty^-\Vert=v_\infty"),
            ("Positiver Hyperbel-Skalenbetrag", r"a_h=\frac{\mu_p}{v_\infty^2}>0,\qquad a=-a_h"),
            ("Zeit seit der Periapsis", r"t(H)-t_{\mathrm{p}}=\sqrt{\frac{a_h^3}{\mu_p}}(e_h\sinh H-H)"),
        ),
        "Die Relativgeschwindigkeit an der SOI ist noch nicht v∞. Es gelten v∞ > 0 und e_h > 1.\nDer aktuelle Planner propagiert die Passage und misst Ereignisse numerisch.",
        "Die relative Geschwindigkeit an einem endlichen Abstand enthält noch "
        "planetare Fluchtenergie. Der hyperbolische Überschuss folgt aus der "
        "Zwei-Körper-Energie. Ein passiver Swing-by erhält seinen Betrag und dreht "
        "seine Richtung. Die Hyperbelhalbachse ist negativ; ihr Skalenbetrag positiv.",
    ),
    "solar-asymptote": FormulaCard(
        "Energie und solare Zielasymptote",
        "Oskulierende Zwei-Körper-Hyperbel · ε > 0, e > 1 und nicht verschwindender Drehimpuls",
        (
            ("Spezifische Energie und Fluchtüberschuss", r"\varepsilon=\frac{v^2}{2}-\frac{\mu_\odot}{r},\qquad v_\infty=\sqrt{2\varepsilon}"),
            ("Drehimpuls und Exzentrizitätsvektor", r"\mathbf{h}=\mathbf{r}\times\mathbf{v},\qquad\mathbf{e}=\frac{\mathbf{v}\times\mathbf{h}}{\mu_\odot}-\frac{\mathbf{r}}{r}"),
            ("Basis der Bahnebene", r"\hat{\mathbf{p}}=\frac{\mathbf{e}}{e},\qquad\hat{\mathbf{h}}=\frac{\mathbf{h}}{\Vert\mathbf{h}\Vert},\qquad\hat{\mathbf{q}}=\hat{\mathbf{h}}\times\hat{\mathbf{p}}"),
            ("Asymptotische wahre Anomalie", r"\nu_\infty=\arccos(-1/e),\qquad e=\Vert\mathbf{e}\Vert"),
            ("Ausgehende Asymptotenrichtung", r"\hat{\mathbf{s}}_\infty=\hat{\mathbf{p}}\cos\nu_\infty+\hat{\mathbf{q}}\sin\nu_\infty"),
            ("Winkelrest zum normierten Zielvektor", r"\alpha=\arccos\left[\operatorname{clip}(\hat{\mathbf{s}}_\infty\cdot\hat{\mathbf{t}},-1,1)\right]"),
        ),
        "Gebundene, parabolische und radial degenerierte Zustände besitzen diese Hyperbelbasis nicht.\nUnter zusätzlichen Kräften muss die oskulierende Richtung im weiteren Verlauf geprüft werden.",
        "Energie, Drehimpuls und Exzentrizitätsvektor bestimmen die ausgehende "
        "solare Hyperbelasymptote. Der Zielwinkel ergibt sich aus dem Skalarprodukt "
        "normierter Richtungen. Das Argument des Arkuskosinus wird gegen Rundung begrenzt.",
    ),
}


def render_card(name: str, card: FormulaCard, output: Path, preview: Path | None) -> dict:
    height = HEADER + ROW_HEIGHT * len(card.rows) + FOOTER
    fig = plt.figure(figsize=(WIDTH / 72, height / 72), dpi=72, facecolor="#ffffff")
    try:
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set(xlim=(0, WIDTH), ylim=(height, 0))
        ax.axis("off")
        ax.add_patch(Rectangle((0, 0), WIDTH, 6, color="#166c91", linewidth=0))
        texts = [
            ax.text(30, 35, card.title, fontsize=21, weight="bold", color="#102c43", va="center"),
            ax.text(30, 66, card.subtitle, fontsize=11, color="#526879", va="center"),
        ]
        equations = []
        for index, (label, equation) in enumerate(card.rows):
            top = HEADER + index * ROW_HEIGHT
            if index % 2 == 0:
                ax.add_patch(Rectangle((20, top), WIDTH - 40, ROW_HEIGHT, color="#f2f6fa", linewidth=0))
            texts.append(ax.text(32, top + 17, f"{index + 1:02d}", fontsize=10,
                                 family="monospace", color="#166c91", va="center"))
            texts.append(ax.text(67, top + 17, label, fontsize=10, color="#526879", va="center"))
            item = ax.text(67, top + 52, f"${equation}$", fontsize=21, color="#102c43", va="center")
            equations.append((item, top))
            texts.append(item)
        texts.append(ax.text(30, height - FOOTER + 23, card.note, fontsize=10,
                             color="#526879", va="top", linespacing=1.7))
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
        for item in texts:
            box = item.get_window_extent(renderer)
            if box.x0 < 18 or box.x1 > WIDTH - 18 or box.y0 < 10 or box.y1 > height - 10:
                raise ValueError(f"Clipped text in {name}: {item.get_text()}")
        for item, top in equations:
            box = item.get_window_extent(renderer)
            if box.y0 < height - top - ROW_HEIGHT + 4 or box.y1 > height - top - 29:
                raise ValueError(f"Equation overlaps its row in {name}: {item.get_text()}")
        path = output / f"{name}.svg"
        with StringIO() as buffer:
            fig.savefig(buffer, format="svg", metadata={"Date": None, "Title": card.title})
            svg = buffer.getvalue()
        # Include native accessible SVG labels; glyph paths keep rendering independent of fonts.
        marker = 'version="1.1">'
        if marker not in svg:
            raise ValueError("Unexpected Matplotlib SVG header")
        svg = svg.replace(marker,
                          'version="1.1" role="img" aria-labelledby="formula-title formula-description">\n'
                          f'<title id="formula-title">{escape(card.title)}</title>\n'
                          f'<desc id="formula-description">{escape(card.description)}</desc>', 1)
        svg = "\n".join(line.rstrip() for line in svg.splitlines()) + "\n"
        path.write_text(svg, encoding="utf-8", newline="\n")
        if preview:
            fig.savefig(preview / f"{name}.png", dpi=120)
        return {"file": path.name, "rows": len(card.rows), "layout": "PASSED"}
    finally:
        plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "docs/assets/formulas")
    parser.add_argument("--preview-dir", type=Path, help="Optional PNG previews for visual review")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.preview_dir:
        args.preview_dir.mkdir(parents=True, exist_ok=True)
    with matplotlib.rc_context({"font.family": "DejaVu Sans", "mathtext.fontset": "stix",
                                "svg.fonttype": "path", "svg.hashsalt": "solar-system-readme"}):
        results = [render_card(name, card, args.output_dir, args.preview_dir) for name, card in CARDS.items()]
    print(json.dumps({"status": "PASSED", "cards": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
