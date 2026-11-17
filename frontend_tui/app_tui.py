"""
Frontend TUI (Terminal User Interface) para el Sistema Multi-Liga de Análisis Lineal.

Arquitectura en 3 Capas (Pressman & Ghezzi - Separación de Intereses):
- Capa de Presentación (este módulo): Interfaz gráfica en terminal con paneles Unicode,
  cancha táctica ASCII/Unicode, gráficos de barras comparativos, selector Multi-Liga y
  Simulador de Fichajes / Jugadores Nuevos (Cold-Start Híbrido).
- Capa de Backend (C++17 `bin/lpf_backend.exe`): Servidor HTTP/JSON multihilo con
  ThreadPool que ejecuta toda la lógica de negocio y Álgebra Lineal (matrices A_E,
  pseudoinversa de Moore-Penrose, transferencia matricial inter-club, optimización
  combinatoria y predicción bilateral inter-liga).
- Capa de Datos (`data/lpf_analytics.db` + `database/db_manager.py`): Base de datos
  relacional SQLite3 alimentada por `sofascore_modulo.py`.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DB_PATH = PROJECT_ROOT / "data" / "lpf_analytics.db"
DEFAULT_SERVER_URL = "http://127.0.0.1:8085"


def resolve_backend_executable() -> Path:
    """Busca dinámicamente el ejecutable del Backend C++ en las carpetas de build estándar."""
    exe_name = "lpf_backend.exe" if os.name == "nt" else "lpf_backend"
    candidates = [
        PROJECT_ROOT / "bin" / exe_name,
        PROJECT_ROOT / "cmake-build-release" / exe_name,
        PROJECT_ROOT / "cmake-build-debug" / exe_name,
        PROJECT_ROOT / "build" / exe_name,
    ]
    for cand in candidates:
        if cand.exists():
            return cand
    return candidates[0]


def ensure_runtime_dependencies() -> Path:
    """
    Garantiza de forma dinámica y multi-dispositivo que:
    1. La base de datos SQLite3 (`data/lpf_analytics.db`) esté inicializada.
    2. `sqlite3.dll` esté disponible junto al ejecutable C++ en Windows.
    """
    if not DB_PATH.exists():
        from database.db_manager import DatabaseManager
        mgr = DatabaseManager(DB_PATH)
        mgr.import_from_csvs()

    backend_exe = resolve_backend_executable()
    if os.name == "nt":
        target_dll = backend_exe.parent / "sqlite3.dll"
        if not target_dll.exists():
            try:
                import _sqlite3
                src_dll = Path(_sqlite3.__file__).resolve().parent / "sqlite3.dll"
                if src_dll.exists():
                    target_dll.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_dll, target_dll)
            except Exception:
                pass
    return backend_exe


# ==============================================================================
# ESTILOS ANSI Y COMPONENTES GRÁFICOS DE TERMINAL (TUI)
# ==============================================================================

class AnsiStyle:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    MAGENTA = "\033[95m"
    BLUE = "\033[94m"
    WHITE = "\033[97m"


def enable_windows_ansi() -> None:
    if os.name == "nt":
        os.system("")
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass


class TuiRenderer:
    """Motor de renderizado visual en consola (paneles, tablas, cancha táctica y barras)."""

    WIDTH = 108

    @classmethod
    def clear_screen(cls) -> None:
        os.system("cls" if os.name == "nt" else "clear")

    @classmethod
    def header_banner(cls, server_status: Optional[Dict[str, Any]] = None) -> None:
        w = cls.WIDTH
        print(f"{AnsiStyle.CYAN}╔{'═' * (w - 2)}╗{AnsiStyle.RESET}")
        title = "⚽ SISTEMA MULTI-LIGA DE TRANSFORMACIONES LINEALES Y PREDICCIÓN TÁCTICA ⚽"
        sub = "Motor Matemático C++17 | Resolución Cold-Start Híbrida para Jugadores Nuevos | SQLite3 Multi-Liga"
        print(f"{AnsiStyle.CYAN}║{AnsiStyle.BOLD}{AnsiStyle.WHITE}{title.center(w - 2)}{AnsiStyle.RESET}{AnsiStyle.CYAN}║{AnsiStyle.RESET}")
        print(f"{AnsiStyle.CYAN}║{AnsiStyle.YELLOW}{sub.center(w - 2)}{AnsiStyle.RESET}{AnsiStyle.CYAN}║{AnsiStyle.RESET}")
        if server_status and server_status.get("status") == "ok":
            comps_n = server_status.get("competitions_loaded", 12)
            st_line = (
                f"[Backend C++: ONLINE]  Hilos: {server_status.get('worker_threads')}  |  "
                f"Ligas: {comps_n}  |  Equipos: {server_status.get('teams_loaded')}  |  "
                f"Consultas: {server_status.get('completed_tasks')}  |  "
                f"Tiempo C++: {server_status.get('total_compute_us')} µs"
            )
            print(f"{AnsiStyle.CYAN}╠{'─' * (w - 2)}╣{AnsiStyle.RESET}")
            print(f"{AnsiStyle.CYAN}║{AnsiStyle.GREEN}{st_line.center(w - 2)}{AnsiStyle.RESET}{AnsiStyle.CYAN}║{AnsiStyle.RESET}")
        print(f"{AnsiStyle.CYAN}╚{'═' * (w - 2)}╝{AnsiStyle.RESET}")

    @classmethod
    def section_title(cls, text: str) -> None:
        w = cls.WIDTH
        print(f"\n{AnsiStyle.MAGENTA}┌{'─' * (w - 2)}┐{AnsiStyle.RESET}")
        print(f"{AnsiStyle.MAGENTA}│ {AnsiStyle.BOLD}{AnsiStyle.WHITE}{text.ljust(w - 4)}{AnsiStyle.RESET}{AnsiStyle.MAGENTA} │{AnsiStyle.RESET}")
        print(f"{AnsiStyle.MAGENTA}└{'─' * (w - 2)}┘{AnsiStyle.RESET}")

    @classmethod
    def render_tactical_pitch(
        cls,
        team_name: str,
        formation: str,
        players: List[Dict[str, Any]],
    ) -> None:
        """Dibuja una cancha de fútbol en terminal ubicando a los titulares por línea posicional."""
        gks = [p for p in players if p.get("position_code") == "GK"]
        defs = [p for p in players if p.get("position_code") == "DEF"]
        mids = [p for p in players if p.get("position_code") == "MID"]
        fwds = [p for p in players if p.get("position_code") == "FWD"]

        def format_tag(p: Dict[str, Any]) -> str:
            num = p.get("jersey_number") or "-"
            sname = p.get("short_name") or p.get("name", "?")
            if len(sname) > 13:
                sname = sname[:12] + "."
            score = p.get("tactical_score", 0.0)
            new_mark = "★" if p.get("is_new_signing") else ""
            return f"[{num}]{new_mark}{sname}({score:.1f})"

        def format_line(group: List[Dict[str, Any]], width: int) -> str:
            if not group:
                return "".center(width)
            tags = [format_tag(p) for p in group]
            joined = "   ".join(tags)
            if len(joined) > width:
                tags_short = [f"[{p.get('jersey_number','-')}] {(p.get('short_name') or p.get('name','?'))[:10]}" for p in group]
                joined = "  ".join(tags_short)
            return joined.center(width)

        pw = 100
        header = f" CANCHA TÁCTICA: {team_name.upper()} | FORMACIÓN: {formation} "
        print(f"\n  {AnsiStyle.GREEN}╔{header.center(pw, '═')}╗{AnsiStyle.RESET}")
        print(f"  {AnsiStyle.GREEN}║{'┌──────────────────────────┐'.center(pw)}║{AnsiStyle.RESET}")
        print(f"  {AnsiStyle.GREEN}║{AnsiStyle.BOLD}{AnsiStyle.WHITE}{format_line(gks, pw)}{AnsiStyle.RESET}{AnsiStyle.GREEN}║{AnsiStyle.RESET}")
        print(f"  {AnsiStyle.GREEN}║{'└───────────┬──────────────┘'.center(pw)}║{AnsiStyle.RESET}")
        print(f"  {AnsiStyle.GREEN}║{AnsiStyle.CYAN}{format_line(defs, pw)}{AnsiStyle.RESET}{AnsiStyle.GREEN}║{AnsiStyle.RESET}")
        print(f"  {AnsiStyle.GREEN}║{('─' * (pw - 8)).center(pw)}║{AnsiStyle.RESET}")
        print(f"  {AnsiStyle.GREEN}║{'( ⚽ )'.center(pw)}║{AnsiStyle.RESET}")
        print(f"  {AnsiStyle.GREEN}║{AnsiStyle.YELLOW}{format_line(mids, pw)}{AnsiStyle.RESET}{AnsiStyle.GREEN}║{AnsiStyle.RESET}")
        print(f"  {AnsiStyle.GREEN}║{'┌───────────┴──────────────┐'.center(pw)}║{AnsiStyle.RESET}")
        print(f"  {AnsiStyle.GREEN}║{AnsiStyle.RED}{AnsiStyle.BOLD}{format_line(fwds, pw)}{AnsiStyle.RESET}{AnsiStyle.GREEN}║{AnsiStyle.RESET}")
        print(f"  {AnsiStyle.GREEN}║{'└──────────────────────────┘'.center(pw)}║{AnsiStyle.RESET}")
        print(f"  {AnsiStyle.GREEN}╚{'═' * pw}╝{AnsiStyle.RESET}")

    @classmethod
    def render_metrics_comparison_table(
        cls,
        metrics_list: List[Dict[str, Any]],
        title: str,
        ref_label: str = "Histórico",
    ) -> None:
        cls.section_title(title)
        header = (
            f" {'#':>2} │ {'Métrica de Partido (Vector en R^20)':<36} │ "
            f"{'Proyectado':>11} │ {ref_label:>10} │ {'Dif (Δ)':>9} │ {'Impacto Visual':<20}"
        )
        print(f"{AnsiStyle.BOLD}{header}{AnsiStyle.RESET}")
        print("─" * cls.WIDTH)

        for item in metrics_list:
            idx = item["index"] + 1
            name = item["name"]
            unit = item["unit"]
            val = item["value"]
            hist = item["historical_avg"]
            delta = item["delta"]
            higher_better = item["higher_is_better"]

            is_good = (delta >= 0.0) if higher_better else (delta <= 0.0)
            color = AnsiStyle.GREEN if is_good else AnsiStyle.YELLOW
            if abs(delta) < 0.03:
                color = AnsiStyle.WHITE

            ratio = (val / hist) if hist > 1e-4 else 1.0
            bars_count = max(1, min(18, int(round(ratio * 9))))
            bar_str = "█" * bars_count + "░" * (18 - bars_count)

            sign = "+" if delta > 0 else ""
            val_str = f"{val:7.2f} {unit:<4}"
            hist_str = f"{hist:7.2f}"
            delta_str = f"{sign}{delta:6.2f}"

            print(
                f" {idx:2d} │ {name:<36} │ "
                f"{AnsiStyle.BOLD}{val_str:>11}{AnsiStyle.RESET} │ "
                f"{hist_str:>10} │ {color}{delta_str:>9}{AnsiStyle.RESET} │ "
                f"{color}{bar_str}{AnsiStyle.RESET}"
            )
        print("─" * cls.WIDTH)

    @classmethod
    def render_matchup_view(cls, data: Dict[str, Any]) -> None:
        ta = data["team_a"]
        tb = data["team_b"]
        p_win_a = ta["win_probability"]
        p_draw = data["draw_probability"]
        p_win_b = tb["win_probability"]
        is_inter = data.get("is_inter_league", False)
        rel_ratio = data.get("relative_league_ratio", 1.0)

        cls.section_title(
            f"PREDICCIÓN BILATERAL: {ta['name'].upper()} ({ta['formation']}) vs {tb['name'].upper()} ({tb['formation']})"
        )

        comp_a = ta.get("competition_name", "Liga Profesional Argentina")
        comp_b = tb.get("competition_name", "Liga Profesional Argentina")
        str_a = ta.get("strength_factor", 1.0)
        str_b = tb.get("strength_factor", 1.0)
        mode_tag = (
            f"{AnsiStyle.MAGENTA}[CRUCE INTER-LIGA / INTERNACIONAL | Ratio Fuerza λ_A/λ_B = {rel_ratio:.2f}]{AnsiStyle.RESET}"
            if is_inter
            else f"{AnsiStyle.GREEN}[DUELO MISMA LIGA ({comp_a})]{AnsiStyle.RESET}"
        )
        print(
            f"  🌍 {AnsiStyle.BOLD}Competiciones:{AnsiStyle.RESET} "
            f"{ta['name']} [{comp_a}, λ={str_a:.2f}]  vs  {tb['name']} [{comp_b}, λ={str_b:.2f}]  {mode_tag}"
        )

        total_bar_len = 72
        len_a = max(1, int(round((p_win_a / 100.0) * total_bar_len)))
        len_d = max(1, int(round((p_draw / 100.0) * total_bar_len)))
        len_b = max(1, total_bar_len - len_a - len_d)

        print(
            f"\n  {AnsiStyle.GREEN}{AnsiStyle.BOLD}{ta['name']} ({p_win_a:.1f}%){AnsiStyle.RESET}"
            f"{'Empate (' + f'{p_draw:.1f}%' + ')':^34}"
            f"{AnsiStyle.CYAN}{AnsiStyle.BOLD}{tb['name']} ({p_win_b:.1f}%){AnsiStyle.RESET}"
        )
        bar = (
            f"  {AnsiStyle.GREEN}{'█' * len_a}{AnsiStyle.RESET}"
            f"{AnsiStyle.YELLOW}{'▒' * len_d}{AnsiStyle.RESET}"
            f"{AnsiStyle.CYAN}{'█' * len_b}{AnsiStyle.RESET}"
        )
        print(bar)

        print(
            f"\n  ⚽ {AnsiStyle.BOLD}Goles Esperados (xG Acoplado):{AnsiStyle.RESET} "
            f"{AnsiStyle.GREEN}{ta['name']} {ta['expected_goals']:.2f}{AnsiStyle.RESET}  vs  "
            f"{AnsiStyle.CYAN}{tb['expected_goals']:.2f} {tb['name']}{AnsiStyle.RESET}   │   "
            f"Índice Táctico: {ta['tactical_edge']:.1f} vs {tb['tactical_edge']:.1f}"
        )

        scores_str = "   ".join(
            f"[{s['goals_a']}-{s['goals_b']}: {s['probability']:.1f}%]"
            for s in data.get("top_scorelines", [])
        )
        print(f"  🎯 {AnsiStyle.BOLD}Marcadores más probables:{AnsiStyle.RESET} {AnsiStyle.YELLOW}{scores_str}{AnsiStyle.RESET}\n")

        print("─" * cls.WIDTH)
        print(
            f" {'#':>2} │ {'Métrica (Espacio R^20)':<32} │ "
            f"{ta['name'][:14]:>14} │ {'Duelo Visual':^22} │ {tb['name'][:14]:<14} │ {'Ventaja':<10}"
        )
        print("─" * cls.WIDTH)

        for row in data["comparison_table"]:
            idx = row["index"] + 1
            mname = row["name"][:32]
            va = row["team_a_coupled"]
            vb = row["team_b_coupled"]
            hb = row["higher_is_better"]

            denom = va + vb
            share_a = (va / denom) if denom > 1e-6 else 0.5
            cells_a = max(1, min(19, int(round(share_a * 20))))
            cells_b = 20 - cells_a
            duel_bar = f"{AnsiStyle.GREEN}{'◄' * cells_a}{AnsiStyle.CYAN}{'►' * cells_b}{AnsiStyle.RESET}"

            if abs(va - vb) < 0.05:
                edge = "Parejo"
                edge_col = AnsiStyle.WHITE
            else:
                a_wins = (va > vb) if hb else (va < vb)
                edge = ta["name"][:10] if a_wins else tb["name"][:10]
                edge_col = AnsiStyle.GREEN if a_wins else AnsiStyle.CYAN

            print(
                f" {idx:2d} │ {mname:<32} │ "
                f"{AnsiStyle.GREEN}{va:14.2f}{AnsiStyle.RESET} │ "
                f" {duel_bar} │ "
                f"{AnsiStyle.CYAN}{vb:<14.2f}{AnsiStyle.RESET} │ "
                f"{edge_col}{edge:<10}{AnsiStyle.RESET}"
            )
        print("─" * cls.WIDTH)

    @classmethod
    def render_transfer_simulation_view(cls, res: Dict[str, Any]) -> None:
        sim_p = res["simulated_player"]
        cls.section_title(
            f"SIMULACIÓN DE FICHAJE / JUGADOR NUEVO EN EL CLUB: {sim_p['name'].upper()} -> {res['target_team_name'].upper()}"
        )
        print(
            f"  👤 {AnsiStyle.BOLD}Jugador Incorporado:{AnsiStyle.RESET} {AnsiStyle.CYAN}{sim_p['name']}{AnsiStyle.RESET} "
            f"({sim_p['position']} - {sim_p['position_code']})  │  "
            f"Origen: {AnsiStyle.YELLOW}{res['source_team_name']}{AnsiStyle.RESET} ({res['source_competition_name']}, λ_orig={res['source_league_strength']:.2f})"
        )
        print(
            f"  🏟️  {AnsiStyle.BOLD}Club Destino:{AnsiStyle.RESET}        {AnsiStyle.GREEN}{res['target_team_name']}{AnsiStyle.RESET} "
            f"({res['target_competition_name']}, λ_dest={res['target_league_strength']:.2f})  │  "
            f"Escala Inter-Liga ρ = (λ_orig/λ_dest)^0.65 = {AnsiStyle.MAGENTA}{res['league_scaling_ratio']:.3f}{AnsiStyle.RESET}"
        )
        print(
            f"  🔄 {AnsiStyle.BOLD}Sustitución en el 11:{AnsiStyle.RESET} Ingresa {AnsiStyle.GREEN}{sim_p['name']} (s_new = {res['new_player_score']:.2f}){AnsiStyle.RESET} "
            f"por {AnsiStyle.YELLOW}{res['replaced_player_name']} (s_old = {res['replaced_player_score']:.2f}){AnsiStyle.RESET}"
        )
        sign = "+" if res["tactical_score_delta"] >= 0 else ""
        color_delta = AnsiStyle.GREEN if res["tactical_score_delta"] >= 0 else AnsiStyle.YELLOW
        print(
            f"  📈 {AnsiStyle.BOLD}Impacto Funcional J(y):{AnsiStyle.RESET} Base = {res['baseline_tactical_score']:.2f}  ->  "
            f"Con Fichaje = {color_delta}{AnsiStyle.BOLD}{res['post_transfer_tactical_score']:.2f} "
            f"({sign}{res['tactical_score_delta']:.2f} pts | {sign}{res['improvement_pct']:.2f}%){AnsiStyle.RESET}"
        )

        cls.render_tactical_pitch(
            f"{res['target_team_name']} (Con {sim_p['short_name']})",
            f"{res['formation']} ({res['strategy']})",
            res["post_transfer_starters"],
        )

        cls.render_metrics_comparison_table(
            res["metrics_comparison"],
            f"IMPACTO EN LAS 20 MÉTRICAS DEL EQUIPO: 11 CON {sim_p['name'].upper()} vs 11 BASE ({res['target_team_name']})",
            ref_label="11 Base",
        )


# ==============================================================================
# CLIENTE HTTP CONCURRENTE Y GESTOR DEL CICLO DE VIDA DEL BACKEND C++
# ==============================================================================

class BackendCppClient:
    """Cliente HTTP/JSON desacoplado que se comunica con el servidor Backend en C++."""

    def __init__(self, base_url: str = DEFAULT_SERVER_URL) -> None:
        self.base_url = base_url.rstrip("/")
        self._spawned_process: Optional[subprocess.Popen] = None

    def request(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        if params:
            clean_params = {k: v for k, v in params.items() if v is not None and v != ""}
            if clean_params:
                url = f"{url}?{urlencode(clean_params)}"

        req = Request(url, headers={"Accept": "application/json"}, method="GET")
        with urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw)

    def is_alive(self) -> bool:
        try:
            st = self.request("/api/status")
            return st.get("status") == "ok"
        except Exception:
            return False

    def ensure_server_running(self, port: int = 8085) -> Dict[str, Any]:
        if self.is_alive():
            return self.request("/api/status")

        backend_exe = ensure_runtime_dependencies()
        if not backend_exe.exists():
            raise RuntimeError(
                f"No se encontró el ejecutable del Backend C++ en {backend_exe}. "
                "Ejecutá primero .\\compilar_cpp.ps1 o compilá desde CMake/CLion."
            )

        self._spawned_process = subprocess.Popen(
            [str(backend_exe), "--db", str(DB_PATH), "--port", str(port), "--threads", "12"],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        for _ in range(30):
            time.sleep(0.15)
            if self.is_alive():
                return self.request("/api/status")

        raise RuntimeError("El servidor Backend C++ no respondió en el puerto esperado.")

    def close(self) -> None:
        if self._spawned_process is not None:
            self._spawned_process.terminate()
            try:
                self._spawned_process.wait(timeout=3)
            except Exception:
                self._spawned_process.kill()
            self._spawned_process = None


# ==============================================================================
# CONTROLADOR INTERACTIVO DE LA APLICACIÓN TUI MULTI-LIGA
# ==============================================================================

class LpfAnalyticsTuiApp:
    """Controlador de casos de uso de la interfaz TUI Multi-Liga en terminal."""

    def __init__(self, client: BackendCppClient) -> None:
        self.client = client
        self.competitions_cache: List[Dict[str, Any]] = []
        self.teams_cache: List[Dict[str, Any]] = []

    def refresh_competitions(self) -> List[Dict[str, Any]]:
        resp = self.client.request("/api/competitions")
        self.competitions_cache = resp.get("competitions", [])
        return self.competitions_cache

    def refresh_teams(self, competition_id: int = 0) -> List[Dict[str, Any]]:
        params = {"competition_id": competition_id} if competition_id > 0 else None
        resp = self.client.request("/api/teams", params)
        if competition_id == 0:
            self.teams_cache = resp.get("teams", [])
        return resp.get("teams", [])

    def select_team_interactive(
        self,
        prompt_label: str = "Seleccioná un equipo",
        allow_competition_filter: bool = True,
    ) -> Optional[Dict[str, Any]]:
        if not self.teams_cache:
            self.refresh_teams(0)

        active_teams = self.teams_cache
        if allow_competition_filter:
            print(
                f"\n  {AnsiStyle.BOLD}Filtro de Liga / Competición:{AnsiStyle.RESET} "
                "[Enter = Todas las Ligas | 1 = LPF Argentina | 2 = Ligas Europeas y Brasileirão]"
            )
            f_choice = input("  👉 Filtro rápido [Enter/1/2]: ").strip()
            if f_choice == "1":
                active_teams = [t for t in self.teams_cache if t.get("competition_id") == 72]
            elif f_choice == "2":
                active_teams = [t for t in self.teams_cache if t.get("competition_id") != 72]

        TuiRenderer.section_title(f"CATÁLOGO DE EQUIPOS MULTI-LIGA ({len(active_teams)} EQUIPOS DISPONIBLES)")
        cols = 3
        rows = (len(active_teams) + cols - 1) // cols
        for r in range(rows):
            line_parts = []
            for c in range(cols):
                idx = r + c * rows
                if idx < len(active_teams):
                    t = active_teams[idx]
                    comp_tag = "LPF" if t.get("competition_id") == 72 else t.get("competition_name", "")[:6]
                    entry = f"[{idx + 1:2d}] {t['name'][:17]:<17} ({comp_tag})"
                    line_parts.append(f"{entry:<34}")
            print("  " + " ".join(line_parts))

        print(
            f"\n  {AnsiStyle.DIM}Podés ingresar el # de lista [1-{len(active_teams)}], el ID o parte del nombre "
            f"(ej: 'boca', 'river', 'real madrid', 'city', 'barcelona', 'flamengo').{AnsiStyle.RESET}"
        )
        raw = input(f"  👉 {prompt_label} (o Enter para cancelar): ").strip()
        if not raw:
            return None

        if raw.isdigit():
            val = int(raw)
            if 1 <= val <= len(active_teams):
                return active_teams[val - 1]
            for t in self.teams_cache:
                if t["team_id"] == val:
                    return t

        q = raw.lower()
        for t in active_teams:
            if q in t["name"].lower() or q in t["short_name"].lower():
                return t
        for t in self.teams_cache:
            if q in t["name"].lower() or q in t["short_name"].lower():
                return t

        print(f"  {AnsiStyle.RED}[!] No se encontró un equipo que coincida con '{raw}'.{AnsiStyle.RESET}")
        return None

    def select_strategy_interactive(self) -> str:
        print(f"\n  {AnsiStyle.BOLD}Perfiles Tácticos de Evaluación Lineal J(y) = w^T * y:{AnsiStyle.RESET}")
        print("    [1] BALANCED   - Balanceado General (Recomendado)")
        print("    [2] OFFENSIVE  - Ofensivo / Vertical (Prioriza goles, tiros al arco y área rival)")
        print("    [3] DEFENSIVE  - Defensivo / Bloque Sólido (Minimiza goles/tiros en contra, prioriza quite)")
        print("    [4] POSSESSION - Posesión y Asociativo (Prioriza tenencia, pases y valoración)")
        choice = input("  👉 Elegí el perfil táctico [1-4, Enter=1]: ").strip()
        mapping = {"1": "BALANCED", "2": "OFFENSIVE", "3": "DEFENSIVE", "4": "POSSESSION"}
        return mapping.get(choice, "BALANCED")

    def select_formation_interactive(self) -> str:
        print(f"\n  {AnsiStyle.BOLD}Formaciones Tácticas Reglamentarias:{AnsiStyle.RESET}")
        print("    [1] AUTO    (Evalúa todas y elige la formación de máximo rendimiento)")
        print("    [2] 4-3-3   (1 GK, 4 DEF, 3 MID, 3 FWD)")
        print("    [3] 4-4-2   (1 GK, 4 DEF, 4 MID, 2 FWD)")
        print("    [4] 4-2-3-1 (1 GK, 4 DEF, 5 MID, 1 FWD)")
        print("    [5] 3-5-2   (1 GK, 3 DEF, 5 MID, 2 FWD)")
        print("    [6] 5-3-2   (1 GK, 5 DEF, 3 MID, 2 FWD)")
        choice = input("  👉 Elegí la formación [1-6, Enter=1]: ").strip()
        mapping = {
            "1": "AUTO",
            "2": "4-3-3",
            "3": "4-4-2",
            "4": "4-2-3-1",
            "5": "3-5-2",
            "6": "5-3-2",
        }
        return mapping.get(choice, "AUTO")

    def display_squad_table(self, players: List[Dict[str, Any]], team_name: str) -> None:
        eligible_count = sum(1 for p in players if p.get("currently_in_club", True))
        new_signings_count = sum(1 for p in players if p.get("is_new_signing", False))
        departed_count = len(players) - eligible_count
        TuiRenderer.section_title(
            f"PLANTEL DE {team_name.upper()} (Total: {len(players)} │ Elegibles: {eligible_count} │ Nuevos Cold-Start: {new_signings_count} │ Bajas: {departed_count})"
        )
        print(
            f" {'#':>2} │ {'Dorsal':>6} │ {'Jugador':<22} │ {'Posición':<11} │ "
            f"{'Estado / Origen Cold-Start':<22} │ {'PJ/Cv':>5} │ {'α_emp':>5} │ {'Rat.':>4} │ {'Score s_j':>9}"
        )
        print("─" * TuiRenderer.WIDTH)
        for idx, p in enumerate(players, start=1):
            in_club = p.get("currently_in_club", True)
            is_new = p.get("is_new_signing", False)
            origin = p.get("origin_type", "ESTABLISHED")
            prev_team = p.get("previous_team_name", "")
            alpha = p.get("adaptation_alpha", 1.0)

            if not in_club:
                status_lbl = f"{AnsiStyle.RED}{'BAJA (No Elegible)':<22}{AnsiStyle.RESET}"
            elif is_new and origin == "TRANSFERRED":
                tag = f"NUEVO(Ex:{prev_team[:10]})"
                status_lbl = f"{AnsiStyle.MAGENTA}{tag:<22}{AnsiStyle.RESET}"
            elif is_new:
                status_lbl = f"{AnsiStyle.YELLOW}{'NUEVO(Prior Posic.)':<22}{AnsiStyle.RESET}"
            elif origin == "TRANSFERRED" and prev_team:
                tag = f"ACTIVO(Ex:{prev_team[:9]})"
                status_lbl = f"{AnsiStyle.GREEN}{tag:<22}{AnsiStyle.RESET}"
            else:
                status_lbl = f"{AnsiStyle.GREEN}{'ACTIVO (Consolidado)':<22}{AnsiStyle.RESET}"

            pj_str = f"{p['matches_played']:2d}/{p['convocations']:2d}"
            pos_lbl = f"{p['position'][:6]}({p['position_code']})"
            row_style = "" if in_club else AnsiStyle.DIM
            print(
                f"{row_style} {idx:2d} │ {p['jersey_number'] or '-':>6} │ {p['name'][:22]:<22} │ "
                f"{pos_lbl:<11} │ {status_lbl}{row_style} │ "
                f"{pj_str:>5} │ {alpha:5.2f} │ {p['average_rating']:4.2f} │ "
                f"{AnsiStyle.CYAN if in_club else AnsiStyle.DIM}{p['tactical_score']:9.2f}{AnsiStyle.RESET}"
            )
        print("─" * TuiRenderer.WIDTH)

    # --------------------------------------------------------------------------
    # CASO DE USO (a): Ingresar equipo inicial -> Obtener comportamiento esperado
    # --------------------------------------------------------------------------
    def run_use_case_a(self) -> None:
        team = self.select_team_interactive("Seleccioná el equipo para simular su alineación inicial")
        if not team:
            return

        strategy = self.select_strategy_interactive()
        detail = self.client.request("/api/team", {"team_id": team["team_id"], "strategy": strategy})
        players = detail["players"]
        self.display_squad_table(players, team["name"])

        print(f"\n  {AnsiStyle.BOLD}¿Cómo deseás ingresar el equipo inicial (vector x in {{0,1}}^n)?{AnsiStyle.RESET}")
        print("    [1] Ingresar números de orden (# de lista, ej: 1,2,3,4,5,6,7,8,9,10,11)")
        print("    [2] Partir de los 11 con más continuidad y realizar sustituciones (ej. probar a un jugador NUEVO)")
        mode = input("  👉 Opción [1-2, Enter=2]: ").strip() or "2"

        selected_pids: List[int] = []
        if mode == "1":
            raw_list = input("  👉 Ingresá los # de lista separados por coma (ej: 1,2,3,4,5,6,7,8,9,10,11): ").strip()
            for token in raw_list.split(","):
                token = token.strip()
                if token.isdigit():
                    idx = int(token) - 1
                    if 0 <= idx < len(players):
                        cand = players[idx]
                        if not cand.get("currently_in_club", True):
                            print(
                                f"  {AnsiStyle.RED}[!] '{cand['name']}' ya no pertenece al equipo actualmente y no es elegible (omitido).{AnsiStyle.RESET}"
                            )
                            continue
                        selected_pids.append(cand["player_id"])
        else:
            eligible_players = [p for p in players if p.get("currently_in_club", True)]
            gks = [p for p in eligible_players if p["position_code"] == "GK"]
            field = [p for p in eligible_players if p["position_code"] != "GK"]
            base_11 = (gks[:1] + field[:10]) if gks else eligible_players[:11]
            selected_pids = [p["player_id"] for p in base_11]

            print(f"\n  {AnsiStyle.GREEN}Base titular elegible cargada (11 jugadores activos):{AnsiStyle.RESET}")
            for i, p in enumerate(base_11, start=1):
                print(f"    {i:2d}. [{p['position_code']}] #{p['jersey_number'] or '-'} {p['name']} (PJ: {p['matches_played']})")

            swap_raw = input(
                "\n  👉 Si deseás cambiar algún jugador (incluyendo jugadores NUEVOS con 0 PJ), ingresá '#lista_entra=#lista_sale' (Enter para confirmar): "
            ).strip()
            if swap_raw:
                player_by_idx = {i + 1: p for i, p in enumerate(players)}
                for pair in swap_raw.split(","):
                    if "=" in pair:
                        in_s, out_s = pair.split("=", 1)
                        if in_s.strip().isdigit() and out_s.strip().isdigit():
                            p_in = player_by_idx.get(int(in_s.strip()))
                            p_out = player_by_idx.get(int(out_s.strip()))
                            if p_in and not p_in.get("currently_in_club", True):
                                print(
                                    f"  {AnsiStyle.RED}[!] '{p_in['name']}' ya no pertenece al club y no puede ingresar.{AnsiStyle.RESET}"
                                )
                                continue
                            if p_in and p_out and p_out["player_id"] in selected_pids and p_in["player_id"] not in selected_pids:
                                selected_pids[selected_pids.index(p_out["player_id"])] = p_in["player_id"]

        if not selected_pids:
            print(f"  {AnsiStyle.RED}[!] No se seleccionaron jugadores válidos.{AnsiStyle.RESET}")
            return

        csv_pids = ",".join(str(pid) for pid in selected_pids)
        res = self.client.request(
            "/api/predict-behavior",
            {"team_id": team["team_id"], "players": csv_pids, "strategy": strategy},
        )

        TuiRenderer.render_tactical_pitch(
            res["team_name"],
            f"Alineación Ingresada ({res['selected_count']} jugadores)",
            res["active_players"],
        )

        bvec_str = "".join(str(b) for b in res["binary_vector"])
        print(
            f"\n  🧮 {AnsiStyle.BOLD}Vector de Alineación x in {{0,1}}^{len(res['binary_vector'])}:{AnsiStyle.RESET} "
            f"[{AnsiStyle.CYAN}{bvec_str}{AnsiStyle.RESET}] (||x||_1 = {sum(res['binary_vector'])})"
        )
        print(
            f"  📈 {AnsiStyle.BOLD}Ajuste del Modelo Lineal:{AnsiStyle.RESET} R² = {res['r_squared']:.4f}  │  "
            f"Residuo de Frobenius ||Y - A_E*X||_F = {res['frobenius_residual']:.2f}  │  "
            f"Score Táctico J(A_E*x) = {AnsiStyle.GREEN}{res['tactical_score']:.2f}{AnsiStyle.RESET}"
        )

        TuiRenderer.render_metrics_comparison_table(
            res["metrics_comparison"],
            f"COMPORTAMIENTO PROYECTADO DEL EQUIPO: y_hat = T_E(x) = A_E * x ({res['team_name']})",
        )

    # --------------------------------------------------------------------------
    # CASO DE USO (b): Ingresar equipo -> Obtener el 11 inicial óptimo
    # --------------------------------------------------------------------------
    def run_use_case_b(self) -> None:
        team = self.select_team_interactive("Seleccioná el equipo para optimizar su 11 inicial")
        if not team:
            return

        strategy = self.select_strategy_interactive()
        formation = self.select_formation_interactive()

        res = self.client.request(
            "/api/optimize-lineup",
            {"team_id": team["team_id"], "strategy": strategy, "formation": formation},
        )

        TuiRenderer.render_tactical_pitch(
            res["team_name"],
            f"{res['formation']} ({res['strategy']})",
            res["starters"],
        )

        TuiRenderer.section_title(
            f"ONCE INICIAL ÓPTIMO (x*_j = 1) Y SUPLENTES SUGERIDOS (x*_j = 0) - {res['team_name'].upper()} ({res.get('competition_name','')})"
        )
        print(
            f" {'Rol':<10} │ {'Pos':<4} │ {'Dorsal':>6} │ {'Jugador':<24} │ "
            f"{'Origen / Estado':<18} │ {'PJ/Cv':>5} │ {'Rating':>6} │ {'Score Lineal s_j':>16}"
        )
        print("─" * TuiRenderer.WIDTH)
        for p in res["starters"]:
            pj_s = f"{p['matches_played']:2d}/{p['convocations']:2d}"
            orig_lbl = (
                f"Nuevo({p.get('previous_team_name','Prior')[:10]})"
                if p.get("is_new_signing")
                else "Consolidado"
            )
            print(
                f" {AnsiStyle.GREEN}{'TITULAR(1)':<10}{AnsiStyle.RESET} │ {p['position_code']:<4} │ "
                f"{p['jersey_number'] or '-':>6} │ {AnsiStyle.BOLD}{p['name'][:24]:<24}{AnsiStyle.RESET} │ "
                f"{orig_lbl:<18} │ {pj_s:>5} │ {p['average_rating']:6.2f} │ "
                f"{AnsiStyle.GREEN}{p['tactical_score']:16.2f}{AnsiStyle.RESET}"
            )
        print("─" * TuiRenderer.WIDTH)
        for p in res["bench"]:
            pj_s = f"{p['matches_played']:2d}/{p['convocations']:2d}"
            orig_lbl = (
                f"Nuevo({p.get('previous_team_name','Prior')[:10]})"
                if p.get("is_new_signing")
                else "Consolidado"
            )
            print(
                f" {AnsiStyle.YELLOW}{'BANCO  (0)':<10}{AnsiStyle.RESET} │ {p['position_code']:<4} │ "
                f"{p['jersey_number'] or '-':>6} │ {p['name'][:24]:<24} │ "
                f"{orig_lbl:<18} │ {pj_s:>5} │ {p['average_rating']:6.2f} │ "
                f"{AnsiStyle.YELLOW}{p['tactical_score']:16.2f}{AnsiStyle.RESET}"
            )
        print("─" * TuiRenderer.WIDTH)

        sign = "+" if res["improvement_pct"] >= 0 else ""
        print(
            f"  🏆 {AnsiStyle.BOLD}Puntaje Funcional Óptimo J(A_E * x*):{AnsiStyle.RESET} "
            f"{AnsiStyle.GREEN}{res['total_tactical_score']:.2f}{AnsiStyle.RESET} "
            f"({sign}{res['improvement_pct']:.2f}% respecto al promedio histórico del equipo)"
        )

        TuiRenderer.render_metrics_comparison_table(
            res["metrics_comparison"],
            f"RENDIMIENTO ESPERADO CON EL 11 ÓPTIMO ({res['team_name']} - {res['formation']})",
        )

    # --------------------------------------------------------------------------
    # CASO DE USO (c): Predicción entre dos equipos (Misma Liga o Inter-Liga)
    # --------------------------------------------------------------------------
    def run_use_case_c(self) -> None:
        team_a = self.select_team_interactive("Seleccioná el EQUIPO A (Local / Neutral)")
        if not team_a:
            return
        team_b = self.select_team_interactive("Seleccioná el EQUIPO B (Visitante / Rival)")
        if not team_b:
            return

        strategy = self.select_strategy_interactive()
        neutral_in = input("  👉 ¿Disputar en cancha neutral (ej. Final Internacional)? [s/N]: ").strip().lower()
        neutral_flag = 1 if neutral_in in ("s", "si", "sí", "y", "yes") else 0

        res = self.client.request(
            "/api/predict-matchup",
            {
                "team_a_id": team_a["team_id"],
                "team_b_id": team_b["team_id"],
                "strategy": strategy,
                "formation_a": "AUTO",
                "formation_b": "AUTO",
                "neutral": neutral_flag,
            },
        )
        TuiRenderer.render_matchup_view(res)

    # --------------------------------------------------------------------------
    # OPCIÓN 4: Inspeccionar la Matriz de Transformación Lineal A_E in R^(20 x n)
    # --------------------------------------------------------------------------
    def run_inspect_matrix(self) -> None:
        team = self.select_team_interactive("Seleccioná el equipo para inspeccionar su matriz A_E")
        if not team:
            return

        detail = self.client.request("/api/team", {"team_id": team["team_id"], "strategy": "BALANCED"})
        players = detail["players"]
        mat = detail["transformation_matrix"]

        self.display_squad_table(players, detail["name"])

        TuiRenderer.section_title(
            f"MATRIZ DE TRANSFORMACIÓN LINEAL A_E in R^({detail['matrix_rows']} x {detail['matrix_cols']}) - {detail['name'].upper()}"
        )
        print(
            f"  Liga: {detail.get('competition_name','LPF')} (λ={detail.get('strength_factor', 1.0):.2f})  │  "
            f"Dimensión: {detail['matrix_rows']} × {detail['matrix_cols']}  │  "
            f"R² = {detail['r_squared']:.4f}  │  ||Y - A_E*X||_F = {detail['frobenius_residual']:.2f}"
        )
        print(f"  {AnsiStyle.DIM}Mostrando las primeras 8 columnas de A_E:{AnsiStyle.RESET}\n")

        cols_to_show = min(8, len(players))
        col_headers = " │ ".join(f"{(p['short_name'] or p['name'])[:9]:>9}" for p in players[:cols_to_show])
        print(f" {'Métrica (Fila i)':<26} │ {col_headers}")
        print("─" * TuiRenderer.WIDTH)

        metric_names = [
            "1. Goles a favor", "2. Goles en contra", "3. Tiros al arco", "4. Posesión (%)",
            "5. Salvadas", "6. Córners", "7. Faltas", "8. Pases", "9. Tiros libres",
            "10. Amarillas", "11. Rojas", "12. Valoración prom.", "13. Tiros totales",
            "14. Toques área rival", "15. Offsides", "16. Tacles", "17. Intercepciones",
            "18. Recuperaciones", "19. Despejes", "20. Tiros rival arco"
        ]
        for r_idx, m_label in enumerate(metric_names):
            vals = " │ ".join(f"{mat[r_idx][c_idx]:9.3f}" for c_idx in range(cols_to_show))
            print(f" {m_label:<26} │ {vals}")
        print("─" * TuiRenderer.WIDTH)

    # --------------------------------------------------------------------------
    # OPCIÓN 5: Simulador de Fichajes y Jugador Nuevo en un Club (Cold-Start Híbrido)
    # --------------------------------------------------------------------------
    def run_transfer_and_new_player_simulator(self) -> None:
        TuiRenderer.section_title(
            "SIMULADOR DE FICHAJES Y RESOLUCIÓN DE JUGADOR NUEVO EN UN CLUB (MODELO COLD-START HÍBRIDO)"
        )
        print("    [1] Simular Fichaje de un jugador de otro club/liga (Transferencia Matricial + Fuerza de Liga)")
        print("    [2] Simular / Dar de alta un Jugador Nuevo Debutante o Externo (Prior Posicional del Club + Rating)")
        mode = input("  👉 Elegí el modo [1-2, Enter=1]: ").strip() or "1"

        target_team = self.select_team_interactive("Seleccioná el CLUB DESTINO donde se incorporará el jugador")
        if not target_team:
            return

        strategy = self.select_strategy_interactive()
        formation = self.select_formation_interactive()

        if mode == "1":
            source_team = self.select_team_interactive("Seleccioná el CLUB DE ORIGEN del jugador a fichar")
            if not source_team:
                return
            src_detail = self.client.request("/api/team", {"team_id": source_team["team_id"], "strategy": strategy})
            src_players = src_detail["players"]
            self.display_squad_table(src_players, source_team["name"])

            raw_idx = input(f"  👉 Ingresá el # de lista [1-{len(src_players)}] del jugador de {source_team['name']} a transferir: ").strip()
            if not raw_idx.isdigit() or not (1 <= int(raw_idx) <= len(src_players)):
                print(f"  {AnsiStyle.RED}[!] Selección inválida.{AnsiStyle.RESET}")
                return
            chosen_player = src_players[int(raw_idx) - 1]

            res = self.client.request(
                "/api/simulate-transfer",
                {
                    "target_team_id": target_team["team_id"],
                    "source_team_id": source_team["team_id"],
                    "player_id": chosen_player["player_id"],
                    "strategy": strategy,
                    "formation": formation,
                },
            )
            TuiRenderer.render_transfer_simulation_view(res)

            persist = input(
                f"\n  👉 ¿Deseás registrar permanentemente a {chosen_player['name']} como refuerzo nuevo (0 PJ) en {target_team['name']}? [s/N]: "
            ).strip().lower()
            if persist in ("s", "si", "sí", "y"):
                from database.db_manager import DatabaseManager
                mgr = DatabaseManager(DB_PATH)
                mgr.register_new_player(
                    team_id=target_team["team_id"],
                    player_name=chosen_player["name"],
                    position_code=chosen_player["position_code"],
                    jersey_number=chosen_player.get("jersey_number", ""),
                    prior_rating=chosen_player.get("average_rating", 7.0),
                    previous_team_id=source_team["team_id"],
                    player_id=chosen_player["player_id"],
                )
                self.client.request("/api/reload")
                self.refresh_teams(0)
                print(f"  {AnsiStyle.GREEN}[OK] {chosen_player['name']} registrado en {target_team['name']} y matriz A_E recargada en C++. {AnsiStyle.RESET}")
        else:
            p_name = input("  👉 Nombre del nuevo jugador (ej. 'Franco Mastantuono' o 'Refuerzo Extranjero'): ").strip() or "Nuevo Refuerzo"
            print("  Posiciones tácticas: [1] GK (Portero) | [2] DEF (Defensor) | [3] MID (Mediocampista) | [4] FWD (Delantero)")
            pos_opt = input("  👉 Elegí la posición [1-4, Enter=4]: ").strip() or "4"
            pos_code = {"1": "GK", "2": "DEF", "3": "MID", "4": "FWD"}.get(pos_opt, "FWD")
            rat_raw = input("  👉 Valoración previa estimada [5.5 - 9.0, Enter=7.05]: ").strip()
            try:
                prior_rat = float(rat_raw) if rat_raw else 7.05
            except ValueError:
                prior_rat = 7.05

            str_raw = input("  👉 Coeficiente de fuerza de su liga de origen [0.70 - 1.35, Enter=1.00 (LPF)]: ").strip()
            try:
                src_str = float(str_raw) if str_raw else 1.00
            except ValueError:
                src_str = 1.00

            res = self.client.request(
                "/api/simulate-transfer",
                {
                    "target_team_id": target_team["team_id"],
                    "source_team_id": 0,
                    "player_id": 0,
                    "player_name": p_name,
                    "position_code": pos_code,
                    "prior_rating": prior_rat,
                    "source_league_strength": src_str,
                    "strategy": strategy,
                    "formation": formation,
                },
            )
            TuiRenderer.render_transfer_simulation_view(res)

            persist = input(
                f"\n  👉 ¿Deseás dar de alta permanentemente a '{p_name}' (0 PJ) en el plantel de {target_team['name']}? [s/N]: "
            ).strip().lower()
            if persist in ("s", "si", "sí", "y"):
                from database.db_manager import DatabaseManager
                mgr = DatabaseManager(DB_PATH)
                new_pid = mgr.register_new_player(
                    team_id=target_team["team_id"],
                    player_name=p_name,
                    position_code=pos_code,
                    jersey_number="99",
                    prior_rating=prior_rat,
                    previous_team_id=0,
                )
                self.client.request("/api/reload")
                self.refresh_teams(0)
                print(
                    f"  {AnsiStyle.GREEN}[OK] '{p_name}' (ID {new_pid}) incorporado a {target_team['name']} con Prior Posicional y matriz A_E actualizada.{AnsiStyle.RESET}"
                )

    # --------------------------------------------------------------------------
    # OPCIÓN 6: Catálogo de Ligas y Competiciones Multi-Liga
    # --------------------------------------------------------------------------
    def run_competitions_catalog_view(self) -> None:
        comps = self.refresh_competitions()
        if not self.teams_cache:
            self.refresh_teams(0)

        TuiRenderer.section_title(f"CATÁLOGO DE LIGAS Y COMPETICIONES MULTI-LIGA ({len(comps)} TORNEOS)")
        print(
            f" {'Código':<13} │ {'ID 365':>6} │ {'SofaID':>6} │ {'Competición':<28} │ "
            f"{'País / Región':<14} │ {'Confed.':<9} │ {'Fuerza λ_L':>10} │ {'Equipos BD':>10}"
        )
        print("─" * TuiRenderer.WIDTH)
        for c in comps:
            cid = c["competition_id"]
            count_t = sum(1 for t in self.teams_cache if t.get("competition_id") == cid)
            print(
                f" {AnsiStyle.CYAN}{c['code']:<13}{AnsiStyle.RESET} │ {cid:6d} │ {c['sofascore_id']:6d} │ "
                f"{AnsiStyle.BOLD}{c['name'][:28]:<28}{AnsiStyle.RESET} │ {c['country'][:14]:<14} │ "
                f"{c['confederation']:<9} │ {AnsiStyle.GREEN}{c['strength_factor']:10.2f}{AnsiStyle.RESET} │ "
                f"{count_t:10d}"
            )
        print("─" * TuiRenderer.WIDTH)
        print(
            f"  💡 {AnsiStyle.DIM}Para descargar/actualizar en vivo cualquier liga desde 365Scores, podés ejecutar:\n"
            f"     python sofascore_modulo.py --competition EPL --max-teams 5 && python database/db_manager.py --init{AnsiStyle.RESET}"
        )

    # --------------------------------------------------------------------------
    # OPCIÓN 7: Prueba de Estrés Concurrente Multi-Usuario contra el Backend C++
    # --------------------------------------------------------------------------
    def run_concurrency_benchmark(self, num_requests: int = 60, concurrency: int = 12) -> None:
        TuiRenderer.section_title(
            f"PRUEBA DE CARGA MULTI-USUARIO CONCURRENTE ({num_requests} CONSULTAS SIMULTÁNEAS EN {concurrency} HILOS)"
        )
        if not self.teams_cache:
            self.refresh_teams(0)

        t_ids = [t["team_id"] for t in self.teams_cache]

        def worker_task(idx: int) -> float:
            t0 = time.perf_counter()
            tid_a = t_ids[idx % len(t_ids)]
            tid_b = t_ids[(idx * 7 + 3) % len(t_ids)]
            if idx % 4 == 0:
                self.client.request("/api/optimize-lineup", {"team_id": tid_a, "strategy": "BALANCED"})
            elif idx % 4 == 1:
                self.client.request("/api/predict-matchup", {"team_a_id": tid_a, "team_b_id": tid_b})
            elif idx % 4 == 2:
                self.client.request("/api/simulate-transfer", {"target_team_id": 866, "source_team_id": 131, "player_id": 90117})
            else:
                self.client.request("/api/team", {"team_id": tid_a})
            return (time.perf_counter() - t0) * 1000.0

        start_wall = time.perf_counter()
        latencies_ms: List[float] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(worker_task, i) for i in range(num_requests)]
            for fut in concurrent.futures.as_completed(futures):
                latencies_ms.append(fut.result())
        total_wall_ms = (time.perf_counter() - start_wall) * 1000.0

        status = self.client.request("/api/status")
        avg_lat = sum(latencies_ms) / len(latencies_ms)
        min_lat = min(latencies_ms)
        max_lat = max(latencies_ms)
        rps = (num_requests / (total_wall_ms / 1000.0)) if total_wall_ms > 0 else 0.0

        print(f"  ✅ {AnsiStyle.GREEN}{AnsiStyle.BOLD}100% de las consultas concurrentes completadas sin errores{AnsiStyle.RESET}")
        print(f"  ⚡ Throughput total:          {AnsiStyle.CYAN}{rps:.1f} consultas/segundo{AnsiStyle.RESET}")
        print(f"  ⏱️  Tiempo total (Wall Clock): {total_wall_ms:.2f} ms para {num_requests} peticiones paralelas")
        print(f"  📊 Latencia por cliente:      Media = {avg_lat:.2f} ms │ Mín = {min_lat:.2f} ms │ Máx = {max_lat:.2f} ms")
        print(f"  🧵 Estado ThreadPool C++:     {status['worker_threads']} hilos worker │ {status['completed_tasks']} tareas acumuladas")

    def run_demo_showcase(self) -> None:
        """Ejecuta una demostración automática de Multi-Liga, Cold-Start de Jugador Nuevo, (a), (b), (c) y concurrencia."""
        status = self.client.ensure_server_running()
        TuiRenderer.header_banner(status)
        self.refresh_competitions()
        self.refresh_teams(0)

        # 0. Catálogo Multi-Liga
        self.run_competitions_catalog_view()

        # 1. Demostración de (b): Equipo óptimo de Boca Juniors (866)
        opt_boca = self.client.request(
            "/api/optimize-lineup",
            {"team_id": 866, "strategy": "BALANCED", "formation": "AUTO"},
        )
        TuiRenderer.render_tactical_pitch(
            opt_boca["team_name"],
            f"{opt_boca['formation']} ({opt_boca['strategy']})",
            opt_boca["starters"],
        )

        # 2. Demostración de (c) Inter-Liga: Real Madrid (LaLiga) vs River Plate (LPF Argentina)
        inter_matchup = self.client.request(
            "/api/predict-matchup",
            {"team_a_id": 131, "team_b_id": 868, "strategy": "BALANCED", "neutral": 1},
        )
        TuiRenderer.render_matchup_view(inter_matchup)

        # 3. Demostración de Simulador de Fichajes / Jugador Nuevo en el Club (Cold-Start Híbrido)
        sim_transfer = self.client.request(
            "/api/simulate-transfer",
            {
                "target_team_id": 866,
                "source_team_id": 131,
                "player_id": 90117,
                "strategy": "OFFENSIVE",
                "formation": "4-3-3",
            },
        )
        TuiRenderer.render_transfer_simulation_view(sim_transfer)

        # 4. Demostración de Concurrencia Multi-Usuario
        self.run_concurrency_benchmark(num_requests=60, concurrency=12)

    def run_interactive_loop(self) -> None:
        while True:
            status = self.client.ensure_server_running()
            TuiRenderer.header_banner(status)
            print(f"\n  {AnsiStyle.BOLD}MENÚ PRINCIPAL - SISTEMA MULTI-LIGA DE TRANSFORMACIONES LINEALES:{AnsiStyle.RESET}")
            print(f"    {AnsiStyle.CYAN}[1]{AnsiStyle.RESET} {AnsiStyle.BOLD}(Punto a){AnsiStyle.RESET} Ingresar un equipo inicial y simular el comportamiento del equipo (T_E(x) = A_E * x)")
            print(f"    {AnsiStyle.CYAN}[2]{AnsiStyle.RESET} {AnsiStyle.BOLD}(Punto b){AnsiStyle.RESET} Obtener el equipo inicial (11 óptimo) de mejor rendimiento posible")
            print(f"    {AnsiStyle.CYAN}[3]{AnsiStyle.RESET} {AnsiStyle.BOLD}(Punto c){AnsiStyle.RESET} Predicción de partido entre dos equipos (Misma Liga o Cruce Inter-Liga)")
            print(f"    {AnsiStyle.CYAN}[4]{AnsiStyle.RESET} Inspeccionar la Matriz de Transformación Lineal A_E (20 × n) y plantel de un equipo")
            print(f"    {AnsiStyle.CYAN}[5]{AnsiStyle.RESET} {AnsiStyle.YELLOW}Simulador de Fichajes y Jugador Nuevo en un Club (Modelo Cold-Start Híbrido){AnsiStyle.RESET}")
            print(f"    {AnsiStyle.CYAN}[6]{AnsiStyle.RESET} {AnsiStyle.GREEN}Catálogo Multi-Liga y Coeficientes de Fuerza de Competiciones (λ_L){AnsiStyle.RESET}")
            print(f"    {AnsiStyle.CYAN}[7]{AnsiStyle.RESET} Monitor de Concurrencia C++ y Prueba de Estrés Multi-Usuario (60 consultas simultáneas)")
            print(f"    {AnsiStyle.CYAN}[0]{AnsiStyle.RESET} Salir")

            choice = input(f"\n  👉 Seleccioná una opción [0-7]: ").strip()
            if choice == "0":
                print(f"\n  {AnsiStyle.GREEN}Cerrando sesión del cliente TUI. ¡Hasta luego!{AnsiStyle.RESET}")
                break
            elif choice == "1":
                self.run_use_case_a()
                input(f"\n  Presioná Enter para volver al menú principal...")
            elif choice == "2":
                self.run_use_case_b()
                input(f"\n  Presioná Enter para volver al menú principal...")
            elif choice == "3":
                self.run_use_case_c()
                input(f"\n  Presioná Enter para volver al menú principal...")
            elif choice == "4":
                self.run_inspect_matrix()
                input(f"\n  Presioná Enter para volver al menú principal...")
            elif choice == "5":
                self.run_transfer_and_new_player_simulator()
                input(f"\n  Presioná Enter para volver al menú principal...")
            elif choice == "6":
                self.run_competitions_catalog_view()
                input(f"\n  Presioná Enter para volver al menú principal...")
            elif choice == "7":
                self.run_concurrency_benchmark()
                input(f"\n  Presioná Enter para volver al menú principal...")


def main() -> None:
    enable_windows_ansi()
    parser = argparse.ArgumentParser(
        description="Frontend TUI Multi-Liga para el Sistema de Análisis Lineal conectado al Backend C++"
    )
    parser.add_argument(
        "--server-url",
        default=DEFAULT_SERVER_URL,
        help=f"URL del servidor Backend C++ (default: {DEFAULT_SERVER_URL})",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Ejecutar demostración completa Multi-Liga, Cold-Start de Jugador Nuevo, (a), (b), (c) y concurrencia",
    )
    args = parser.parse_args()

    client = BackendCppClient(args.server_url)
    app = LpfAnalyticsTuiApp(client)
    try:
        if args.demo:
            app.run_demo_showcase()
        else:
            app.run_interactive_loop()
    finally:
        client.close()


if __name__ == "__main__":
    main()
