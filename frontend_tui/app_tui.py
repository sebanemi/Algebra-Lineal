"""
Frontend TUI (Terminal User Interface) para el Sistema de Análisis Lineal LPF.

Arquitectura en 3 Capas (Pressman & Ghezzi - Separación de Intereses):
- Capa de Presentación (este módulo): Interfaz gráfica en terminal con paneles Unicode,
  cancha táctica ASCII/Unicode, gráficos de barras comparativos y menús interactivos.
- Capa de Backend (C++17 `bin/lpf_backend.exe`): Servidor HTTP/JSON multihilo con
  ThreadPool que ejecuta toda la lógica de negocio y Álgebra Lineal (matrices A_E,
  pseudoinversa de Moore-Penrose, optimización combinatoria y predicción bilateral).
- Capa de Datos (`data/lpf_analytics.db` + `database/db_manager.py`): Base de datos
  relacional SQLite3 en modo WAL alimentada por `sofascore_modulo.py`.
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
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import URLError
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
    BG_DARK = "\033[48;5;235m"
    BG_GREEN_PITCH = "\033[48;5;22m"


def enable_windows_ansi() -> None:
    if os.name == "nt":
        os.system("")
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass


class TuiRenderer:
    """Motor de renderizado visual en consola (paneles, tablas, cancha táctica y barras)."""

    WIDTH = 104

    @classmethod
    def clear_screen(cls) -> None:
        os.system("cls" if os.name == "nt" else "clear")

    @classmethod
    def header_banner(cls, server_status: Optional[Dict[str, Any]] = None) -> None:
        w = cls.WIDTH
        print(f"{AnsiStyle.CYAN}╔{'═' * (w - 2)}╗{AnsiStyle.RESET}")
        title = "⚽ SISTEMA DE TRANSFORMACIONES LINEALES Y PREDICCIÓN TÁCTICA - LPF ARGENTINA ⚽"
        sub = "Arquitectura Cliente-Servidor Concurrente | Motor Matemático C++17 | Base de Datos SQLite3 WAL"
        print(f"{AnsiStyle.CYAN}║{AnsiStyle.BOLD}{AnsiStyle.WHITE}{title.center(w - 2)}{AnsiStyle.RESET}{AnsiStyle.CYAN}║{AnsiStyle.RESET}")
        print(f"{AnsiStyle.CYAN}║{AnsiStyle.YELLOW}{sub.center(w - 2)}{AnsiStyle.RESET}{AnsiStyle.CYAN}║{AnsiStyle.RESET}")
        if server_status and server_status.get("status") == "ok":
            st_line = (
                f"[Backend C++: ONLINE]  Hilos Worker: {server_status.get('worker_threads')}  |  "
                f"Equipos: {server_status.get('teams_loaded')}  |  "
                f"Consultas atendidas: {server_status.get('completed_tasks')}  |  "
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
            return f"[{num}]{sname}({score:.1f})"

        def format_line(group: List[Dict[str, Any]], width: int) -> str:
            if not group:
                return "".center(width)
            tags = [format_tag(p) for p in group]
            joined = "   ".join(tags)
            if len(joined) > width:
                tags_short = [f"[{p.get('jersey_number','-')}] {(p.get('short_name') or p.get('name','?'))[:10]}" for p in group]
                joined = "  ".join(tags_short)
            return joined.center(width)

        pw = 96
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
    ) -> None:
        cls.section_title(title)
        header = (
            f" {'#':>2} │ {'Métrica de Partido (Vector en R^20)':<36} │ "
            f"{'Proyectado':>11} │ {'Histórico':>10} │ {'Dif (Δ)':>9} │ {'Impacto Visual':<20}"
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

            # Barra proporcional respecto al promedio histórico
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

        cls.section_title(
            f"PREDICCIÓN BILATERAL: {ta['name'].upper()} ({ta['formation']}) vs {tb['name'].upper()} ({tb['formation']})"
        )

        # Barra de probabilidades de victoria / empate / visita
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

        # Marcadores más probables
        scores_str = "   ".join(
            f"[{s['goals_a']}-{s['goals_b']}: {s['probability']:.1f}%]"
            for s in data.get("top_scorelines", [])
        )
        print(f"  🎯 {AnsiStyle.BOLD}Marcadores más probables:{AnsiStyle.RESET} {AnsiStyle.YELLOW}{scores_str}{AnsiStyle.RESET}\n")

        # Tabla comparativa de las 20 métricas
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
# CONTROLADOR INTERACTIVO DE LA APLICACIÓN TUI
# ==============================================================================

class LpfAnalyticsTuiApp:
    """Controlador de casos de uso de la interfaz TUI en terminal."""

    def __init__(self, client: BackendCppClient) -> None:
        self.client = client
        self.teams_cache: List[Dict[str, Any]] = []

    def refresh_teams(self) -> List[Dict[str, Any]]:
        resp = self.client.request("/api/teams")
        self.teams_cache = resp.get("teams", [])
        return self.teams_cache

    def select_team_interactive(self, prompt_label: str = "Seleccioná un equipo") -> Optional[Dict[str, Any]]:
        if not self.teams_cache:
            self.refresh_teams()

        TuiRenderer.section_title(f"CATÁLOGO DE EQUIPOS DE LIGA PROFESIONAL ({len(self.teams_cache)} EQUIPOS)")
        cols = 3
        rows = (len(self.teams_cache) + cols - 1) // cols
        for r in range(rows):
            line_parts = []
            for c in range(cols):
                idx = r + c * rows
                if idx < len(self.teams_cache):
                    t = self.teams_cache[idx]
                    entry = f"[{idx + 1:2d}] {t['name'][:20]:<20} (ID:{t['team_id']})"
                    line_parts.append(f"{entry:<33}")
            print("  " + " ".join(line_parts))

        print(f"\n  {AnsiStyle.DIM}Podés ingresar el número de lista [1-{len(self.teams_cache)}], el ID o parte del nombre (ej: 'boca', 'river', 'instituto').{AnsiStyle.RESET}")
        raw = input(f"  👉 {prompt_label} (o Enter para cancelar): ").strip()
        if not raw:
            return None

        if raw.isdigit():
            val = int(raw)
            if 1 <= val <= len(self.teams_cache):
                return self.teams_cache[val - 1]
            for t in self.teams_cache:
                if t["team_id"] == val:
                    return t

        q = raw.lower()
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
        departed_count = len(players) - eligible_count
        TuiRenderer.section_title(
            f"PLANTEL DE {team_name.upper()} (Total: {len(players)} │ Elegibles Hoy: {eligible_count} │ Bajas Históricas: {departed_count})"
        )
        print(
            f" {'#':>2} │ {'Dorsal':>6} │ {'Jugador':<23} │ {'Posición':<12} │ "
            f"{'Estado Club':<14} │ {'PJ/Cv':>5} │ {'Min.':>5} │ {'Rat.':>4} │ {'Score s_j':>9}"
        )
        print("─" * TuiRenderer.WIDTH)
        for idx, p in enumerate(players, start=1):
            in_club = p.get("currently_in_club", True)
            status_lbl = (
                f"{AnsiStyle.GREEN}{'ACTIVO (Eleg.)':<14}{AnsiStyle.RESET}"
                if in_club
                else f"{AnsiStyle.RED}{'BAJA (No Eleg)':<14}{AnsiStyle.RESET}"
            )
            pj_str = f"{p['matches_played']:2d}/{p['convocations']:2d}"
            pos_lbl = f"{p['position'][:7]}({p['position_code']})"
            row_style = "" if in_club else AnsiStyle.DIM
            print(
                f"{row_style} {idx:2d} │ {p['jersey_number'] or '-':>6} │ {p['name'][:23]:<23} │ "
                f"{pos_lbl:<12} │ {status_lbl}{row_style} │ "
                f"{pj_str:>5} │ {p['total_minutes']:5d} │ {p['average_rating']:4.2f} │ "
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
        print("    [2] Partir de los 11 con más partidos jugados y realizar sustituciones")
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
            # Filtrar únicamente jugadores que pertenecen actualmente al plantel (elegibles)
            eligible_players = [p for p in players if p.get("currently_in_club", True)]
            gks = [p for p in eligible_players if p["position_code"] == "GK"]
            field = [p for p in eligible_players if p["position_code"] != "GK"]
            base_11 = (gks[:1] + field[:10]) if gks else eligible_players[:11]
            selected_pids = [p["player_id"] for p in base_11]

            print(f"\n  {AnsiStyle.GREEN}Base titular elegible cargada (11 jugadores activos con mayor continuidad):{AnsiStyle.RESET}")
            for i, p in enumerate(base_11, start=1):
                print(f"    {i:2d}. [{p['position_code']}] #{p['jersey_number'] or '-'} {p['name']} (PJ: {p['matches_played']})")

            swap_raw = input(
                "\n  👉 Si deseás cambiar algún jugador, ingresá '#lista_entra=#lista_sale' (o varios separados por coma, Enter para confirmar estos 11): "
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
            f"ONCE INICIAL ÓPTIMO (x*_j = 1) Y SUPLENTES SUGERIDOS (x*_j = 0) - {res['team_name'].upper()}"
        )
        print(
            f" {'Rol':<10} │ {'Pos':<4} │ {'Dorsal':>6} │ {'Jugador':<26} │ "
            f"{'PJ/Conv':>7} │ {'Minutos':>7} │ {'Rating':>6} │ {'Score Lineal s_j':>16}"
        )
        print("─" * TuiRenderer.WIDTH)
        for p in res["starters"]:
            pj_s = f"{p['matches_played']:2d}/{p['convocations']:2d}"
            print(
                f" {AnsiStyle.GREEN}{'TITULAR(1)':<10}{AnsiStyle.RESET} │ {p['position_code']:<4} │ "
                f"{p['jersey_number'] or '-':>6} │ {AnsiStyle.BOLD}{p['name'][:26]:<26}{AnsiStyle.RESET} │ "
                f"{pj_s:>7} │ {p['total_minutes']:7d} │ {p['average_rating']:6.2f} │ "
                f"{AnsiStyle.GREEN}{p['tactical_score']:16.2f}{AnsiStyle.RESET}"
            )
        print("─" * TuiRenderer.WIDTH)
        for p in res["bench"]:
            pj_s = f"{p['matches_played']:2d}/{p['convocations']:2d}"
            print(
                f" {AnsiStyle.YELLOW}{'BANCO  (0)':<10}{AnsiStyle.RESET} │ {p['position_code']:<4} │ "
                f"{p['jersey_number'] or '-':>6} │ {p['name'][:26]:<26} │ "
                f"{pj_s:>7} │ {p['total_minutes']:7d} │ {p['average_rating']:6.2f} │ "
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
    # CASO DE USO (c): Predicción entre dos equipos
    # --------------------------------------------------------------------------
    def run_use_case_c(self) -> None:
        team_a = self.select_team_interactive("Seleccioná el EQUIPO LOCAL (Equipo A)")
        if not team_a:
            return
        team_b = self.select_team_interactive("Seleccioná el EQUIPO VISITANTE (Equipo B)")
        if not team_b:
            return

        strategy = self.select_strategy_interactive()
        res = self.client.request(
            "/api/predict-matchup",
            {
                "team_a_id": team_a["team_id"],
                "team_b_id": team_b["team_id"],
                "strategy": strategy,
                "formation_a": "AUTO",
                "formation_b": "AUTO",
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

        TuiRenderer.section_title(
            f"MATRIZ DE TRANSFORMACIÓN LINEAL A_E in R^({detail['matrix_rows']} x {detail['matrix_cols']}) - {detail['name'].upper()}"
        )
        print(
            f"  Dimensión: {detail['matrix_rows']} filas (métricas) × {detail['matrix_cols']} columnas (jugadores)  │  "
            f"R² = {detail['r_squared']:.4f}  │  ||Y - A_E*X||_F = {detail['frobenius_residual']:.2f}"
        )
        print(f"  {AnsiStyle.DIM}Mostrando las primeras 8 columnas (jugadores con mayor continuidad en el plantel):{AnsiStyle.RESET}\n")

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
    # OPCIÓN 5: Prueba de Estrés Concurrente Multi-Usuario contra el Backend C++
    # --------------------------------------------------------------------------
    def run_concurrency_benchmark(self, num_requests: int = 60, concurrency: int = 12) -> None:
        TuiRenderer.section_title(
            f"PRUEBA DE CARGA MULTI-USUARIO CONCURRENTE ({num_requests} CONSULTAS SIMULTÁNEAS EN {concurrency} HILOS)"
        )
        if not self.teams_cache:
            self.refresh_teams()

        t_ids = [t["team_id"] for t in self.teams_cache]

        def worker_task(idx: int) -> float:
            t0 = time.perf_counter()
            tid_a = t_ids[idx % len(t_ids)]
            tid_b = t_ids[(idx * 7 + 3) % len(t_ids)]
            if idx % 3 == 0:
                self.client.request("/api/optimize-lineup", {"team_id": tid_a, "strategy": "BALANCED"})
            elif idx % 3 == 1:
                self.client.request("/api/predict-matchup", {"team_a_id": tid_a, "team_b_id": tid_b})
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
        """Ejecuta una demostración automática de los puntos (a), (b), (c) y concurrencia."""
        status = self.client.ensure_server_running()
        TuiRenderer.header_banner(status)
        self.refresh_teams()

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
        TuiRenderer.render_metrics_comparison_table(
            opt_boca["metrics_comparison"],
            f"[PUNTO B] ONCE INICIAL ÓPTIMO Y RENDIMIENTO ESPERADO: {opt_boca['team_name'].upper()} ({opt_boca['formation']})",
        )

        # 2. Demostración de (a): Simulación de comportamiento de Instituto (7272) con un 11 inicial elegible
        detail_inst = self.client.request("/api/team", {"team_id": 7272, "strategy": "BALANCED"})
        inst_players = [p for p in detail_inst["players"] if p.get("currently_in_club", True)]
        gks = [p["player_id"] for p in inst_players if p["position_code"] == "GK"][:1]
        field = [p["player_id"] for p in inst_players if p["position_code"] != "GK"][:10]
        pids_csv = ",".join(str(x) for x in (gks + field))
        beh_inst = self.client.request(
            "/api/predict-behavior",
            {"team_id": 7272, "players": pids_csv, "strategy": "BALANCED"},
        )
        TuiRenderer.render_metrics_comparison_table(
            beh_inst["metrics_comparison"],
            f"[PUNTO A] SIMULACIÓN DE COMPORTAMIENTO DADO UN EQUIPO INICIAL: {beh_inst['team_name'].upper()}",
        )

        # 3. Demostración de (c): Predicción entre dos equipos (Boca Juniors vs River Plate)
        matchup = self.client.request(
            "/api/predict-matchup",
            {"team_a_id": 866, "team_b_id": 868, "strategy": "BALANCED"},
        )
        TuiRenderer.render_matchup_view(matchup)

        # 4. Demostración de Concurrencia Multi-Usuario
        self.run_concurrency_benchmark(num_requests=60, concurrency=12)

    def run_interactive_loop(self) -> None:
        while True:
            status = self.client.ensure_server_running()
            TuiRenderer.header_banner(status)
            print(f"\n  {AnsiStyle.BOLD}MENÚ PRINCIPAL - SISTEMA DE TRANSFORMACIONES LINEALES LPF:{AnsiStyle.RESET}")
            print(f"    {AnsiStyle.CYAN}[1]{AnsiStyle.RESET} {AnsiStyle.BOLD}(Punto a){AnsiStyle.RESET} Ingresar un equipo inicial y simular el comportamiento del equipo (T_E(x) = A_E * x)")
            print(f"    {AnsiStyle.CYAN}[2]{AnsiStyle.RESET} {AnsiStyle.BOLD}(Punto b){AnsiStyle.RESET} Obtener el equipo inicial (11 óptimo) de mejor rendimiento posible")
            print(f"    {AnsiStyle.CYAN}[3]{AnsiStyle.RESET} {AnsiStyle.BOLD}(Punto c){AnsiStyle.RESET} Predicción de partido entre dos equipos en base a sus rendimientos lineales")
            print(f"    {AnsiStyle.CYAN}[4]{AnsiStyle.RESET} Inspeccionar la Matriz de Transformación Lineal A_E (20 × n) y plantel de un equipo")
            print(f"    {AnsiStyle.CYAN}[5]{AnsiStyle.RESET} Monitor de Concurrencia C++ y Prueba de Estrés Multi-Usuario (60 consultas simultáneas)")
            print(f"    {AnsiStyle.CYAN}[0]{AnsiStyle.RESET} Salir")

            choice = input(f"\n  👉 Seleccioná una opción [0-5]: ").strip()
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
                self.run_concurrency_benchmark()
                input(f"\n  Presioná Enter para volver al menú principal...")


def main() -> None:
    enable_windows_ansi()
    parser = argparse.ArgumentParser(
        description="Frontend TUI para el Sistema de Análisis Lineal LPF conectado al Backend C++"
    )
    parser.add_argument(
        "--server-url",
        default=DEFAULT_SERVER_URL,
        help=f"URL del servidor Backend C++ (default: {DEFAULT_SERVER_URL})",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Ejecutar demostración completa de los casos (a), (b), (c) y concurrencia y salir",
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
