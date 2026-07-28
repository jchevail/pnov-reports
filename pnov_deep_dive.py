"""
PNOV Deep Dive — Automated Analysis Tool
Scrapes QuickSight for PNOV Deep Dive data, builds HTML report.
UTR: Mother Station + XPTs grouped together
OTR: Each node analyzed separately

Build:
python -m PyInstaller --onefile --windowed --name "PNOV_DeepDive" pnov_deep_dive.py --collect-all selenium --collect-all webdriver_manager
"""
import os
import sys
import glob
import time
import shutil
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext
from datetime import datetime, timedelta
import webbrowser
import subprocess
import csv

from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.firefox import GeckoDriverManager

# ============================================================
# CONFIG
# ============================================================
QUICKSIGHT_URL = (
    "https://us-east-1.quicksight.aws.amazon.com/sn/account/amzlbiaquicksight/"
    "dashboards/43c8d34e-4b14-443b-9f80-2fbb987e6ca2/sheets/"
    "43c8d34e-4b14-443b-9f80-2fbb987e6ca2_1a09bbb8-f1a7-464d-af26-e8acf214d5be"
)
WEEK_CONTROL_ID = "SHEET_CONTROL-ed3b7da8-b6f9-499b-99f6-47211ef13169-id"
PNOV_TARGET = 479

# Mother Station -> XPT mapping
MS_XPT_MAP = {
    "DIF6": ["OMP6"],
    "DWL1": ["OWL7", "OWL1", "OWL6"],
    "DIF3": ["OMP3", "OIF3"],
    "DNC2": ["OBG7", "ONC2"],
    "DAR2": ["OAR5", "OAR2"],
    "DIF4": [],
    "DNC3": ["ONC3", "OAO7", "ONC8", "OAO6"],
    "DPF2": [],
    "DWP1": ["OWP5"],
    "DAO3": ["OAO2", "OBF1"],
    "DND1": ["ONC1"],
    "DIF5": ["OIF6", "OIF5", "OMP5"],
    "DWV1": ["OIF7", "OWV3", "OWV1"],
    "DWP2": [],
    "DAR1": [],
    "DAO2": [],
    "DNC1": [],
    "DAO1": [],
    "DBG2": ["OBG3", "OBG5"],
    "DLP2": [],
    "DLP6": ["OWP9"],
    "DWB2": ["OWL3", "OWB1"],
    "DLP4": [],
    "DAC9": [],
    "DAR8": [],
    "DIF1": ["OHF1", "OIF2"],
    "DAC2": ["OGR1"],
    "DHG1": [],
    "DLP5": [],
    "DWP8": [],
    "DBF1": [],
    "DWB9": [],
    "DWV9": [],
    "DHG8": [],
    "DND2": [],
    "DHG9": [],
    "DRQ6": [],
    "DHG2": [],
    "DAC6": [],
    "DLP9": [],
    "DAR9": [],
    "DRQ5": [],
}

# Build reverse map: XPT -> Mother Station
XPT_TO_MS = {}
for ms, xpts in MS_XPT_MAP.items():
    for xpt in xpts:
        XPT_TO_MS[xpt] = ms

ALL_MOTHER_STATIONS = sorted(MS_XPT_MAP.keys())

# GitHub Pages auto-publish config
GITHUB_REPO_DIR = r"C:\Users\jchevail\pnov-reports"
GIT_EXE = r"C:\Program Files\Git\cmd\git.exe"


def publish_to_github(html_path, log_func=print):
    """Copy report to GitHub repo and push to publish on GitHub Pages."""
    if not os.path.isdir(GITHUB_REPO_DIR):
        log_func(f"  GitHub repo not found at {GITHUB_REPO_DIR} — skipping publish")
        return False

    try:
        # Copy latest report to repo
        dest = os.path.join(GITHUB_REPO_DIR, "index.html")
        shutil.copy2(html_path, dest)

        # Also copy as pnov_LATEST.html
        dest_latest = os.path.join(GITHUB_REPO_DIR, "pnov_LATEST.html")
        shutil.copy2(html_path, dest_latest)

        # Git add, commit, push
        def git(*args):
            result = subprocess.run(
                [GIT_EXE] + list(args),
                cwd=GITHUB_REPO_DIR,
                capture_output=True, text=True, timeout=30
            )
            return result

        git("add", "-A")
        git("commit", "-m", f"Update PNOV report {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        result = git("push", "origin", "main")

        if result.returncode == 0:
            log_func("  ✅ Published to GitHub Pages!")
            log_func("  🔗 https://jchevail.github.io/pnov-reports/")
            return True
        else:
            log_func(f"  ⚠️ Push failed: {result.stderr[:200]}")
            return False
    except Exception as e:
        log_func(f"  ⚠️ GitHub publish error: {e}")
        return False


def get_all_nodes_for_stations(selected_stations):
    """Return exactly the nodes the user entered — no auto-expansion."""
    return set(selected_stations)


def group_nodes_by_ms(selected_nodes):
    """Group the user-selected nodes by their Mother Station for UTR analysis.
    Nodes that share the same MS are grouped together.
    Standalone MS or XPTs whose MS is not in the list are their own group."""
    groups = {}  # ms -> list of nodes in that group
    
    for node in selected_nodes:
        if node in MS_XPT_MAP:
            # It's a Mother Station
            ms = node
        elif node in XPT_TO_MS:
            # It's an XPT — find its MS
            ms = XPT_TO_MS[node]
        else:
            # Unknown node — treat as standalone
            ms = node
        
        if ms not in groups:
            groups[ms] = []
        if node not in groups[ms]:
            groups[ms].append(node)
    
    return groups


# ============================================================
# BROWSER SETUP
# ============================================================
def find_firefox_profile():
    profiles_dir = os.path.join(os.environ.get("APPDATA", ""), "Mozilla", "Firefox", "Profiles")
    if not os.path.exists(profiles_dir):
        return None
    for name in os.listdir(profiles_dir):
        if os.path.isdir(os.path.join(profiles_dir, name)) and "default-esr" in name:
            return os.path.join(profiles_dir, name)
    for name in os.listdir(profiles_dir):
        if os.path.isdir(os.path.join(profiles_dir, name)) and "default" in name:
            return os.path.join(profiles_dir, name)
    return None


def find_firefox_binary():
    for p in [r"C:\Program Files\Mozilla Firefox\firefox.exe",
              r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe"]:
        if os.path.exists(p):
            return p
    return None


def get_downloads_dir():
    return os.path.join(os.environ.get("USERPROFILE", ""), "Downloads")


def create_browser(download_dir=None):
    main_profile = find_firefox_profile()
    firefox_bin = find_firefox_binary()
    if not main_profile or not firefox_bin:
        raise RuntimeError("Firefox not found!")

    scrape_profile = os.path.join(
        os.environ["APPDATA"], "Mozilla", "Firefox", "Profiles", "pnov-deep-dive"
    )
    os.makedirs(scrape_profile, exist_ok=True)

    for fname in ["cookies.sqlite", "key4.db", "cert9.db", "logins.json",
                  "credentialstate.sqlite", "permissions.sqlite"]:
        src = os.path.join(main_profile, fname)
        dst = os.path.join(scrape_profile, fname)
        if os.path.exists(src):
            try:
                shutil.copy2(src, dst)
            except:
                pass

    opts = Options()
    opts.binary_location = firefox_bin
    opts.add_argument("-profile")
    opts.add_argument(scrape_profile)
    opts.add_argument("-no-remote")

    # Prevent restoring previous sessions or interfering with main Firefox
    opts.set_preference("browser.sessionstore.resume_from_crash", False)
    opts.set_preference("browser.startup.homepage_override.mstone", "ignore")
    opts.set_preference("browser.tabs.warnOnClose", False)
    opts.set_preference("browser.warnOnQuit", False)
    opts.set_preference("browser.startup.page", 0)  # blank page on start

    dl_dir = download_dir or get_downloads_dir()
    opts.set_preference("browser.download.folderList", 2)
    opts.set_preference("browser.download.dir", dl_dir)
    opts.set_preference("browser.download.useDownloadDir", True)
    opts.set_preference("browser.helperApps.neverAsk.saveToDisk", "text/csv,application/csv")
    opts.set_preference("browser.download.manager.showWhenStarting", False)

    gecko_path = None
    cache_dir = os.path.join(os.environ.get("USERPROFILE", ""), ".wdm", "drivers", "geckodriver")
    if os.path.exists(cache_dir):
        for root_dir, dirs, files in os.walk(cache_dir):
            if "geckodriver.exe" in files:
                gecko_path = os.path.join(root_dir, "geckodriver.exe")
                break
    if not gecko_path:
        gecko_path = GeckoDriverManager().install()

    service = Service(gecko_path, log_output=os.devnull)
    driver = webdriver.Firefox(service=service, options=opts)
    driver.set_window_size(1920, 1080)
    return driver


# ============================================================
# SCRAPER
# ============================================================
class PNOVScraper:
    def __init__(self, log_func=print, week="", selected_stations=None):
        self.log = log_func
        self.week = week
        self.selected_stations = selected_stations or []
        self.driver = None
        self.download_dir = get_downloads_dir()

    def run(self):
        self.log(f"{'='*50}")
        self.log(f"PNOV Deep Dive — Week {self.week}")
        self.log(f"Stations: {', '.join(self.selected_stations)}")
        self.log(f"{'='*50}")

        all_nodes = get_all_nodes_for_stations(self.selected_stations)
        self.log(f"Nodes to analyze: {sorted(all_nodes)}")

        try:
            self.log("Starting browser...")
            self.driver = create_browser(self.download_dir)

            self.log("Loading QuickSight...")
            self.driver.get(QUICKSIGHT_URL)
            time.sleep(30)  # QuickSight is heavy, needs more time

            if "midway" in self.driver.current_url.lower() or "sso" in self.driver.current_url.lower():
                self.log("  SSO redirect, waiting...")
                time.sleep(20)

            # Dismiss any popups/modals (Amazon Quick "We've been busy building" etc.)
            self.log("  Dismissing popups...")
            self.driver.execute_script("""
                // Close Amazon Quick sidebar (click X button)
                var closeButtons = document.querySelectorAll(
                    'button[aria-label="Close"], button[aria-label="Dismiss"], ' +
                    'button[aria-label="close"], .modal-close, [data-testid="close-button"], ' +
                    'button.close, [aria-label="Close dialog"], [aria-label="Close panel"]'
                );
                closeButtons.forEach(function(btn) { try { btn.click(); } catch(e){} });
                
                // Hide any overlay/modal
                var overlays = document.querySelectorAll('[class*="overlay"], [class*="Overlay"], [class*="modal"], [class*="Modal"]');
                overlays.forEach(function(el) { try { el.style.display = 'none'; } catch(e){} });
                
                // Close Amazon Quick sidebar by clicking its close/dismiss button
                var sidebar = document.querySelector('[class*="QuickPanel"], [class*="quickPanel"], [class*="amazon-quick"]');
                if (sidebar) sidebar.style.display = 'none';
                
                // Also try to close via the X next to "Amazon Quick"
                var allBtns = document.querySelectorAll('button, [role="button"]');
                for (var i = 0; i < allBtns.length; i++) {
                    var btn = allBtns[i];
                    var label = btn.getAttribute('aria-label') || btn.textContent || '';
                    if (label.indexOf('Close') > -1 || label.indexOf('close') > -1 || label === '×' || label === 'x') {
                        try { btn.click(); } catch(e) {}
                    }
                }
            """)
            time.sleep(3)
            
            # Click the main content area to dismiss any remaining overlay
            try:
                main_area = self.driver.find_element(By.CSS_SELECTOR, '[class*="sheet-container"], [class*="Dashboard"], main, [role="main"]')
                main_area.click()
            except:
                try:
                    self.driver.find_element(By.TAG_NAME, "body").click()
                except:
                    pass
            time.sleep(2)
            
            self.log("  Page ready, looking for controls...")

            self.log(f"Selecting week {self.week}...")
            self._select_week()
            time.sleep(15)

            self.log("Exporting CSV...")
            csv_path = self._export_csv()
            if not csv_path:
                raise RuntimeError("CSV download failed")
            self.log(f"CSV: {csv_path}")

            self.log("Parsing...")
            data = self._parse_csv(csv_path)
            self.log(f"  {len(data)} total rows")

            # Filter to our nodes
            data = [r for r in data if r.get("station", "") in all_nodes]
            self.log(f"  {len(data)} rows after station filter")

            self.log("Building report...")
            html = self._build_report(data)

            output_dir = os.path.join(
                os.path.dirname(os.path.abspath(sys.executable if getattr(sys, 'frozen', False) else __file__)),
                "pnov_reports"
            )
            os.makedirs(output_dir, exist_ok=True)
            ts = datetime.now().strftime("%Y-%m-%d_%Hh%M")
            output_file = os.path.join(output_dir, f"pnov_W{self.week}_{ts}.html")
            latest = os.path.join(output_dir, "pnov_LATEST.html")

            with open(output_file, "w", encoding="utf-8") as f:
                f.write(html)
            try:
                with open(latest, "w", encoding="utf-8") as f:
                    f.write(html)
            except:
                pass

            self.log(f"Saved: {output_file}")

            self.log("Publishing to GitHub Pages...")
            publish_to_github(output_file, self.log)

            self.log("Done!")
            return output_file
        finally:
            if self.driver:
                self.driver.quit()

    def _select_week(self):
        # QuickSight may use iframes — try switching into them
        self.log("  Looking for week control...")
        
        # Debug: log what's on the page
        try:
            controls_text = self.driver.execute_script("""
                var el = document.querySelector('[class*="control"], [class*="Control"]');
                return el ? el.textContent.substring(0, 200) : 'no control div found';
            """)
            self.log(f"  Controls area: {controls_text[:100]}")
        except:
            pass

        # First try in main page by ID
        dropdown = None
        try:
            dropdown = WebDriverWait(self.driver, 60).until(
                EC.presence_of_element_located((By.ID, WEEK_CONTROL_ID))
            )
            self.log("  Found by ID!")
        except:
            self.log("  Not found by ID, trying alternatives...")

        # Try by data-automation-context attribute
        if not dropdown:
            try:
                dropdown = self.driver.find_element(
                    By.CSS_SELECTOR, '[data-automation-context*="week"]'
                )
            except:
                pass

        # Try by visible text content "week equals"
        if not dropdown:
            self.log("  Trying by text content...")
            try:
                dropdown = self.driver.execute_script("""
                    var els = document.querySelectorAll('[data-automation-id="sheet_control_value"]');
                    for (var i = 0; i < els.length; i++) {
                        if (els[i].textContent.indexOf('2026') > -1 || els[i].getAttribute('data-automation-context').indexOf('week') > -1) {
                            return els[i];
                        }
                    }
                    // Broader search
                    var all = document.querySelectorAll('div[role="combobox"], div[tabindex="0"]');
                    for (var i = 0; i < all.length; i++) {
                        if (all[i].textContent.match(/\\d{4}-\\d+/)) {
                            return all[i];
                        }
                    }
                    return null;
                """)
            except:
                pass

        # If not found, check iframes
        if not dropdown:
            self.log("  Not in main frame, checking iframes...")
            iframes = self.driver.find_elements(By.TAG_NAME, "iframe")
            self.log(f"  Found {len(iframes)} iframes")
            for i, iframe in enumerate(iframes):
                try:
                    self.driver.switch_to.frame(iframe)
                    dropdown = WebDriverWait(self.driver, 5).until(
                        EC.presence_of_element_located((By.ID, WEEK_CONTROL_ID))
                    )
                    self.log(f"  Found in iframe {i}!")
                    break
                except:
                    self.driver.switch_to.default_content()

        # Still not found — try by data-automation attributes
        if not dropdown:
            self.log("  Trying by automation attributes...")
            self.driver.switch_to.default_content()
            # Check all iframes again with broader search
            iframes = self.driver.find_elements(By.TAG_NAME, "iframe")
            for iframe in iframes:
                try:
                    self.driver.switch_to.frame(iframe)
                    dropdown = WebDriverWait(self.driver, 5).until(
                        EC.presence_of_element_located((
                            By.CSS_SELECTOR, '[data-automation-id="sheet_control_value"]'
                        ))
                    )
                    self.log("  Found via automation attr!")
                    break
                except:
                    self.driver.switch_to.default_content()

        if not dropdown:
            # Last resort: dump page info for debugging
            self.log(f"  Page title: {self.driver.title}")
            self.log(f"  URL: {self.driver.current_url[:100]}")
            self.log(f"  Body text (first 200): {self.driver.find_element(By.TAG_NAME, 'body').text[:200]}")
            raise RuntimeError("Could not find week dropdown control!")

        # Click to open dropdown
        self.log("  Clicking dropdown...")
        self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", dropdown)
        time.sleep(1)
        dropdown.click()
        time.sleep(3)

        # Type in search
        self.log(f"  Searching for {self.week}...")
        try:
            search_input = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 'input[placeholder="Search value"]'))
            )
            search_input.clear()
            search_input.send_keys(self.week)
            time.sleep(2)
        except:
            self.log("  No search input found, trying direct click...")

        # Click the matching week option
        self.driver.execute_script("""
            var items = document.querySelectorAll('li, div[role="option"], span, label');
            for (var i = 0; i < items.length; i++) {
                if (items[i].textContent.trim().indexOf(arguments[0]) > -1 && items[i].offsetParent !== null) {
                    items[i].click();
                    return true;
                }
            }
            return false;
        """, self.week)
        time.sleep(1)

        # Close dropdown
        try:
            self.driver.find_element(By.TAG_NAME, "body").click()
        except:
            pass
        time.sleep(2)
        self.log("  Week selected!")

    def _export_csv(self):
        existing = set(glob.glob(os.path.join(self.download_dir, "Deep_Dive_*.csv")))

        # Find the 3-dot menu button
        menu_btn = WebDriverWait(self.driver, 30).until(
            EC.presence_of_element_located((
                By.CSS_SELECTOR, '[aria-label*="Menu options"][aria-label*="Deep Dive"]'
            ))
        )
        self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", menu_btn)
        time.sleep(1)
        ActionChains(self.driver).move_to_element(menu_btn).perform()
        time.sleep(1)
        menu_btn.click()
        time.sleep(2)

        export_btn = WebDriverWait(self.driver, 10).until(
            EC.element_to_be_clickable((
                By.CSS_SELECTOR, '[data-automation-id="dashboard_visual_dropdown_export"]'
            ))
        )
        export_btn.click()
        self.log("  Waiting for download...")

        for i in range(60):
            time.sleep(2)
            current = set(glob.glob(os.path.join(self.download_dir, "Deep_Dive_*.csv")))
            new = current - existing
            if new:
                path = list(new)[0]
                if not os.path.exists(path + ".part"):
                    time.sleep(2)
                    return path
            if i % 10 == 0 and i > 0:
                self.log(f"  Still waiting... ({i*2}s)")
        return None

    def _parse_csv(self, csv_path):
        rows = []
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            header = None
            for i, row in enumerate(reader):
                if i > 10:
                    break
                row_lower = [c.lower().strip() for c in row]
                if "tracking_id" in row_lower or "station" in row_lower:
                    header = [c.strip() for c in row]
                    break
            if not header:
                f.seek(0)
                header = [c.strip() for c in next(csv.reader(f))]

            f.seek(0)
            found = False
            for row in csv.reader(f):
                if not found:
                    if [c.strip() for c in row] == header:
                        found = True
                    continue
                if len(row) == len(header):
                    rows.append({header[j]: row[j].strip() for j in range(len(header))})
        return rows

    def _sf(self, val):
        try:
            return float(val) if val else 0.0
        except:
            return 0.0


    def _build_report(self, data):
        if not data:
            return "<html><body><h1>No data</h1></body></html>"

        total = len(data)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

        # Per-station sections — grouped by Mother Station for UTR, separate for OTR
        sections_html = ""
        groups = group_nodes_by_ms(self.selected_stations)
        
        for ms, nodes_in_group in groups.items():
            ms_data = [r for r in data if r.get("station", "") in nodes_in_group]
            if not ms_data:
                continue

            n = len(ms_data)
            if len(nodes_in_group) > 1:
                nodes_label = " + ".join(nodes_in_group)
            else:
                nodes_label = nodes_in_group[0]

            # === UTR (grouped: MS + XPTs) ===
            conceded = len([r for r in ms_data if r.get("is_conceded", "").lower() in ("true", "1", "yes")])
            liquid = len([r for r in ms_data if r.get("is_liquid", "").lower() in ("true", "1", "yes")])
            oversize = len([r for r in ms_data if r.get("is_oversize", "").lower() in ("true", "1", "yes")])
            locker = len([r for r in ms_data if r.get("is_locker", "").lower() in ("true", "1", "yes")])
            ps_scan = len([r for r in ms_data if r.get("ps_scan", "").lower() in ("true", "1", "yes")])
            not_stowed = len([r for r in ms_data if not r.get("stow_datetime")])
            swa = len([r for r in ms_data if r.get("is_swa", "").lower() in ("true", "1", "yes")])
            split = len([r for r in ms_data if r.get("split_type", "").lower() not in ("", "none", "non-split")])
            return_d1 = len([r for r in ms_data if r.get("return - debriefed (d+1)", "").lower() in ("true", "1", "yes")])

            # Stow operators (with HV count per operator)
            stow_ops = {}
            for r in ms_data:
                op = r.get("stow_operator", "")
                if op:
                    if op not in stow_ops:
                        stow_ops[op] = {"count": 0, "hv": 0}
                    stow_ops[op]["count"] += 1
                    val = self._sf(r.get("value", r.get("item_value", r.get("parcel_value", 0))))
                    if val >= 100:
                        stow_ops[op]["hv"] += 1
            top_stow = sorted(stow_ops.items(), key=lambda x: -x[1]["count"])[:10]

            # Dwell time
            dwells = [self._sf(r.get("Time between induct and stow [Minutes]", 0)) for r in ms_data if r.get("Time between induct and stow [Minutes]")]
            dw_0_5 = len([t for t in dwells if t <= 5])
            dw_5_15 = len([t for t in dwells if 5 < t <= 15])
            dw_15_30 = len([t for t in dwells if 15 < t <= 30])
            dw_30_60 = len([t for t in dwells if 30 < t <= 60])
            dw_60p = len([t for t in dwells if t > 60])
            dw_total = max(len(dwells), 1)

            stow_rows = ""
            for op, s in top_stow:
                hv_color = "#dc2626" if s["hv"] > 0 else "#999"
                stow_rows += (
                    f"<tr><td>{op}</td><td style='text-align:right'>{s['count']}</td>"
                    f"<td style='text-align:right'>{s['count']/n*100:.1f}%</td>"
                    f"<td style='text-align:right;color:{hv_color}'>{s['hv']}</td></tr>"
                )

            # === ADDITIONAL DATA COMPUTATIONS ===

            # Daily trend
            daily_counts = {}
            for r in ms_data:
                dt_field = r.get("missing_date", r.get("date", r.get("event_date", "")))
                if dt_field:
                    day = dt_field[:10] if len(dt_field) >= 10 else dt_field
                    daily_counts[day] = daily_counts.get(day, 0) + 1
            daily_sorted = sorted(daily_counts.items(), key=lambda x: -x[1])
            peak_days = daily_sorted[:3] if daily_sorted else []

            # Stow rate
            stowed = n - not_stowed
            stow_pct = stowed / n * 100
            mean_dwell = sum(dwells) / max(len(dwells), 1) if dwells else 0

            # Value analysis
            values = [self._sf(r.get("value", r.get("item_value", r.get("parcel_value", 0)))) for r in ms_data]
            total_value = sum(values)
            hv_100 = len([v for v in values if v >= 100])
            hv_300 = len([v for v in values if v >= 300])
            hv_total_value = sum(v for v in values if v >= 100)

            # Characteristics
            repack = len([r for r in ms_data if r.get("is_repack", "").lower() in ("true", "1", "yes")])

            # Recovery
            rts = len([r for r in ms_data if r.get("is_rts", r.get("rts", "")).lower() in ("true", "1", "yes")])
            rts_pct = rts / n * 100

            # Concession details
            concession_late = len([r for r in ms_data if r.get("concession_reason", "").lower() in ("late", "delayed")])
            concession_damaged = len([r for r in ms_data if r.get("concession_reason", "").lower() in ("damaged", "damage")])

            # Fleet / vehicle type
            vehicle_types = {}
            for r in ms_data:
                vt = r.get("vehicle_type", r.get("fleet_type", ""))
                if vt:
                    vehicle_types[vt] = vehicle_types.get(vt, 0) + 1
            lev_count = sum(c for vt, c in vehicle_types.items() if "lev" in vt.lower() or "electric" in vt.lower())
            lev_pct = lev_count / n * 100 if n > 0 else 0

            # Routes distribution
            route_counts = {}
            for r in ms_data:
                rt = r.get("route_id", "")
                if rt:
                    route_counts[rt] = route_counts.get(rt, 0) + 1
            max_per_route = max(route_counts.values()) if route_counts else 0
            num_routes = len(route_counts)

            # PSUA
            psua = len([r for r in ms_data if r.get("is_psua", r.get("psua", "")).lower() in ("true", "1", "yes")])
            psua_pct = psua / n * 100

            # Per-site UTR breakdown note (if multiple nodes in group)
            site_note_html = ""
            if len(nodes_in_group) > 1:
                site_note_html = "<div style='margin-top:10px;padding:10px;background:#f9fafb;border-radius:4px;font-size:11px'><b>Per-site breakdown:</b><br>"
                for node in nodes_in_group:
                    node_rows = [r for r in ms_data if r.get("station", "") == node]
                    nc = len(node_rows)
                    if nc == 0:
                        continue
                    n_liq = len([r for r in node_rows if r.get("is_liquid", "").lower() in ("true", "1", "yes")])
                    n_ov = len([r for r in node_rows if r.get("is_oversize", "").lower() in ("true", "1", "yes")])
                    n_ps = len([r for r in node_rows if r.get("ps_scan", "").lower() in ("true", "1", "yes")])
                    site_note_html += f"• <b>{node}</b>: {nc} parcels | Liquid {n_liq/nc*100:.1f}% | OV {n_ov/nc*100:.1f}% | PS {n_ps/nc*100:.0f}%<br>"
                site_note_html += "</div>"

            # === OTR (per node separately) ===
            otr_sections = ""
            for node in nodes_in_group:
                node_data = [r for r in ms_data if r.get("station", "") == node]
                if not node_data:
                    continue
                nn = len(node_data)

                distances = [self._sf(r.get("distance", 0)) for r in node_data if r.get("distance")]
                u25 = len([d for d in distances if d < 25])
                o25 = len([d for d in distances if d >= 25])
                o100 = len([d for d in distances if d >= 100])
                dt = max(len(distances), 1)

                # Top DAs for this node
                da_map = {}
                for r in node_data:
                    da = r.get("da who marked as missing", "") or r.get("Assigned DA", "")
                    if not da:
                        continue
                    if da not in da_map:
                        da_map[da] = {"count": 0, "conceded": 0, "u25": 0, "o100": 0}
                    da_map[da]["count"] += 1
                    if r.get("is_conceded", "").lower() in ("true", "1", "yes"):
                        da_map[da]["conceded"] += 1
                    d = self._sf(r.get("distance", 0))
                    if d < 25:
                        da_map[da]["u25"] += 1
                    if d >= 100:
                        da_map[da]["o100"] += 1
                top_da = sorted(da_map.items(), key=lambda x: -x[1]["count"])[:10]

                # DSPs for this node
                dsp_map = {}
                for r in node_data:
                    dsp = r.get("dsp", "")
                    if not dsp:
                        continue
                    if dsp not in dsp_map:
                        dsp_map[dsp] = {"count": 0, "conceded": 0, "o100": 0}
                    dsp_map[dsp]["count"] += 1
                    if r.get("is_conceded", "").lower() in ("true", "1", "yes"):
                        dsp_map[dsp]["conceded"] += 1
                    if self._sf(r.get("distance", 0)) >= 100:
                        dsp_map[dsp]["o100"] += 1
                top_dsp = sorted(dsp_map.items(), key=lambda x: -x[1]["count"])[:8]

                da_rows = "".join(
                    f"<tr><td>{'🔴 ' if s['o100']==s['count'] and s['count']>=3 else ''}{da}</td>"
                    f"<td style='text-align:right'>{s['count']}</td>"
                    f"<td style='text-align:right'>{s['conceded']}</td>"
                    f"<td style='text-align:right'>{s['u25']}</td>"
                    f"<td style='text-align:right'>{s['o100']}</td></tr>"
                    for da, s in top_da
                )
                dsp_rows = "".join(
                    f"<tr><td>{dsp}</td><td style='text-align:right'>{s['count']}</td>"
                    f"<td style='text-align:right'>{s['conceded']/s['count']*100:.0f}%</td>"
                    f"<td style='text-align:right'>{s['o100']}</td></tr>"
                    for dsp, s in top_dsp
                )

                node_type = "XPT" if node in XPT_TO_MS else "MS"
                otr_sections += f"""
                <h3>🚚 OTR — {node} ({node_type}) — {nn} parcels</h3>
                <div style="display:grid;grid-template-columns:auto auto auto;gap:10px;margin-bottom:10px">
                    <div style="background:#f0fdf4;padding:8px;border-radius:4px;text-align:center"><b>&lt;25m</b><br>{u25} ({u25/dt*100:.0f}%)</div>
                    <div style="background:#fef9c3;padding:8px;border-radius:4px;text-align:center"><b>25-100m</b><br>{o25-o100} ({(o25-o100)/dt*100:.0f}%)</div>
                    <div style="background:#fef2f2;padding:8px;border-radius:4px;text-align:center"><b>&gt;100m</b><br>{o100} ({o100/dt*100:.0f}%)</div>
                </div>
                <table><thead><tr><th>DA</th><th style="text-align:right">PNOV</th><th style="text-align:right">Conc</th><th style="text-align:right">&lt;25m</th><th style="text-align:right">&gt;100m</th></tr></thead><tbody>{da_rows}</tbody></table>
                <table><thead><tr><th>DSP</th><th style="text-align:right">PNOV</th><th style="text-align:right">Conc%</th><th style="text-align:right">&gt;100m</th></tr></thead><tbody>{dsp_rows}</tbody></table>
                """

            sections_html += f"""
            <div class="station-block">
            <h2>📍 {nodes_label} — {n} PNOV parcels</h2>
            <div class="stats">
                <div class="sc" title="Total PNOV parcels for this station group this week"><div class="l">Total</div><div class="v">{n}</div></div>
                <div class="sc {'red' if conceded/n*100>50 else ''}" title="% of PNOV parcels conceded (refunded to customer = confirmed lost)"><div class="l">Conceded%</div><div class="v">{conceded/n*100:.0f}%</div></div>
                <div class="sc {'red' if ps_scan/n*100<5 else ''}" title="% of PNOV parcels that went through Problem Solve (PS scanned the parcel = investigation done)"><div class="l">PS%</div><div class="v">{ps_scan}/{n} ({ps_scan/n*100:.0f}%)</div></div>
                <div class="sc" title="% of PNOV that are liquid/fragile parcels (higher damage risk)"><div class="l">Liquid%</div><div class="v">{liquid/n*100:.1f}%</div></div>
                <div class="sc" title="% of PNOV that are oversized parcels (OV = stored on rack, not in bags)"><div class="l">OV%</div><div class="v">{oversize/n*100:.1f}%</div></div>
                <div class="sc" title="% of PNOV parcels destined to a locker delivery point"><div class="l">Locker%</div><div class="v">{locker/n*100:.1f}%</div></div>
                <div class="sc {'red' if split/n*100>40 else ''}" title="% of PNOV on split routes (parcel transferred between DAs = higher loss risk)"><div class="l">Split%</div><div class="v">{split/n*100:.1f}%</div></div>
                <div class="sc" title="% of PNOV parcels returned to station D+1 (recovered next day)"><div class="l">RTS D+1%</div><div class="v">{return_d1/n*100:.0f}%</div></div>
            </div>

            <h3>📊 Station Data Summary</h3>
            <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:15px;font-size:12px">
            <div style="background:#f9fafb;padding:10px;border-radius:6px">
                <b>📅 Daily Trend</b><br>
                {''.join(f"• {day}: <b>{cnt}</b> PNOV<br>" for day, cnt in peak_days) if peak_days else '• No date data available'}
                {f"<span style='color:#666'>Spread over {len(daily_counts)} days</span>" if daily_counts else ""}
            </div>
            <div style="background:#f9fafb;padding:10px;border-radius:6px">
                <b>📦 Stow & Dwell</b><br>
                • Stowed: <b>{stow_pct:.1f}%</b> ({stowed}/{n}){f" — {not_stowed} not stowed" if not_stowed else ""}<br>
                • Mean dwell: <b>{mean_dwell:.1f} min</b><br>
                • PS: <b>{ps_scan}/{n} ({ps_scan/n*100:.1f}%)</b>
            </div>
            <div style="background:#f9fafb;padding:10px;border-radius:6px">
                <b>💰 Value</b><br>
                • Total: <b>&euro;{total_value:,.0f}</b><br>
                • HV &gt;100&euro;: <b>{hv_100}</b> parcels (&euro;{hv_total_value:,.0f})<br>
                • HV &gt;300&euro;: <b>{hv_300}</b> parcels
            </div>
            <div style="background:#f9fafb;padding:10px;border-radius:6px">
                <b>🏷️ Characteristics</b><br>
                • Liquid: {liquid} | Repack: {repack} | Locker: {locker}<br>
                • OV: {oversize} | HV&gt;100&euro;: {hv_100} | SWA: {swa}
            </div>
            <div style="background:#f9fafb;padding:10px;border-radius:6px">
                <b>🔄 Recovery & Concession</b><br>
                • RTS: <b>{rts_pct:.0f}%</b> | D+1: <b>{return_d1/n*100:.0f}%</b><br>
                • Conceded: <b>{conceded/n*100:.0f}%</b>{f" (Late: {concession_late}, Damaged: {concession_damaged})" if concession_late or concession_damaged else ""}
            </div>
            <div style="background:#f9fafb;padding:10px;border-radius:6px">
                <b>🚛 Routes & Fleet</b><br>
                • {num_routes} routes (max {max_per_route}/route)<br>
                • Split: <b>{split/n*100:.0f}%</b> | PSUA: <b>{psua_pct:.0f}%</b><br>
                {f"• LEV: <b>{lev_pct:.0f}%</b>" if lev_count > 0 else ""}
                {f"<br>• Fleet: {', '.join(f'{vt} ({c})' for vt, c in sorted(vehicle_types.items(), key=lambda x:-x[1])[:4])}" if vehicle_types else ""}
            </div>
            </div>

            <h3>📦 UTR — Grouped ({nodes_label})</h3>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:15px">
            <div>
                <b>Top Stow Operators</b>
                <table><thead><tr><th>Operator</th><th style="text-align:right">PNOV</th><th style="text-align:right">%</th><th style="text-align:right">HV&gt;100&euro;</th></tr></thead><tbody>{stow_rows}</tbody></table>
            </div>
            <div>
                <b>Induct-to-Stow Dwell Time</b> <span style="font-size:11px;color:#666">(mean: {mean_dwell:.1f} min)</span>
                <table><thead><tr><th>Bucket</th><th style="text-align:right">Count</th><th style="text-align:right">%</th></tr></thead><tbody>
                <tr><td>0-5 min</td><td style="text-align:right">{dw_0_5}</td><td style="text-align:right">{dw_0_5/dw_total*100:.0f}%</td></tr>
                <tr><td>5-15 min</td><td style="text-align:right">{dw_5_15}</td><td style="text-align:right">{dw_5_15/dw_total*100:.0f}%</td></tr>
                <tr><td>15-30 min</td><td style="text-align:right">{dw_15_30}</td><td style="text-align:right">{dw_15_30/dw_total*100:.0f}%</td></tr>
                <tr><td>30-60 min</td><td style="text-align:right">{dw_30_60}</td><td style="text-align:right">{dw_30_60/dw_total*100:.0f}%</td></tr>
                <tr style="background:#fef2f2"><td>&gt;60 min</td><td style="text-align:right">{dw_60p}</td><td style="text-align:right">{dw_60p/dw_total*100:.0f}%</td></tr>
                </tbody></table>
            </div>
            </div>
            {site_note_html}

            {otr_sections}
            """

            # === KEY FINDINGS & ACTION PLAN (UTR + OTR separated) ===
            utr_actions = []
            otr_actions = []

            ps_pct = ps_scan / n * 100
            not_stowed_pct = not_stowed / n * 100
            liquid_pct = liquid / n * 100
            ov_pct = oversize / n * 100
            locker_pct = locker / n * 100
            split_pct = split / n * 100
            conceded_pct = conceded / n * 100
            swa_pct = swa / n * 100

            # ─── UTR ROOT CAUSE ANALYSIS & ACTIONS ───

            # 1. Sort compliance / not stowed
            if not_stowed_pct > 50:
                utr_actions.append(("🔴", "Sort Compliance",
                    f"{not_stowed_pct:.0f}% of PNOV parcels NOT stowed — critical stow gap",
                    "Maintain 99.95% sort scan compliance. Audit induct/stow flow: check HC staffing vs volume, DILO adherence (induct end / stow end variance). Resolve outstanding PS packages immediately. Follow Managing Flow SOP."))
            elif not_stowed_pct > 20:
                utr_actions.append(("🟠", "Sort Compliance",
                    f"{not_stowed_pct:.0f}% of PNOV parcels not stowed",
                    "Deep dive FPY (Inducted Not Containerized). Verify stow buffer is cleared before pick & stage. Ensure ADTA chutes are emptied before cycle end."))

            # 2. Problem Solve
            if ps_pct == 0:
                utr_actions.append(("🔴", "Problem Solve",
                    "PS scan rate at 0% — no Problem Solve intervention detected",
                    "Activate Problem Solve immediately. Parcels flagged by system must be resolved within SLA. Staff PS during sortation + pick & stage. >90% PS resolution target."))
            elif ps_pct < 5:
                utr_actions.append(("🟠", "Problem Solve",
                    f"PS scan rate very low at {ps_pct:.1f}%",
                    "Review PS staffing model. Ensure PS is proactively clearing area on a daily basis. Audit PS parcels not resolved within SLA. Escalate if equipment issue (scanner, TC)."))

            # 3. Top stow operators
            if top_stow and top_stow[0][1]["count"] >= 5:
                top5_str = ", ".join(f"{op} ({s['count']})" for op, s in top_stow[:5])
                top5_total = sum(s["count"] for _, s in top_stow[:5])
                top5_pct = top5_total / n * 100
                top5_hv = sum(s["hv"] for _, s in top_stow[:5])
                utr_actions.append(("🟠", "Stow Contributors",
                    f"Top 5 stowers account for {top5_total} PNOV ({top5_pct:.0f}%): {top5_str}" + (f" — including {top5_hv} HV parcels >100€" if top5_hv > 0 else ""),
                    "Schedule targeted visual audits on these operators using standard checklist (smart stow + tetris). Perform 1:1 coaching with IAC/F2F. Track WoW improvement. If no improvement after 2 weeks, escalate via Performance Management policy."))

            # 4. Dwell time
            if dw_60p > 0 and dw_60p / max(len(dwells), 1) * 100 > 15:
                utr_actions.append(("🟠", "Dwell Time",
                    f"{dw_60p/max(len(dwells),1)*100:.0f}% of parcels have dwell time >60min (induct to stow)",
                    "Reduce stow WIP backlog. Monitor aisle assignments — ensure balanced volume distribution between stowers. Check if DILO is respected (stow end variance). Parcels sitting >60min = higher risk of being left behind."))
            elif dw_30_60 > 0 and (dw_30_60 + dw_60p) / max(len(dwells), 1) * 100 > 30:
                utr_actions.append(("🟡", "Dwell Time",
                    f"{(dw_30_60+dw_60p)/max(len(dwells),1)*100:.0f}% of parcels have dwell time >30min",
                    "Review flow management. Ensure stow operators don't fully empty one aisle before moving to the next. Balance volume between aisles."))

            # 5. Liquid parcels
            if liquid_pct > 10:
                utr_actions.append(("🟠", "Liquid Parcels",
                    f"Liquid parcels at {liquid_pct:.1f}% — abnormally high",
                    "Check daily with PS the correct status of these parcels — are they really missing or actually damaged/returned? Audit repack process. If confirmed damaged, escalate packaging issue."))
            elif liquid_pct > 5:
                utr_actions.append(("🟡", "Liquid Parcels",
                    f"Liquid parcels at {liquid_pct:.1f}%",
                    "Monitor daily with PS. Verify these are genuine missing vs. status errors. Cross-check with damage reports."))

            # 6. OV handling
            if ov_pct > 10:
                utr_actions.append(("🟠", "Oversize Handling",
                    f"OV parcels at {ov_pct:.1f}% — oversized handling gap",
                    "Audit pick & stage process: 1 OV at a time, one-piece flow. Verify OV stowed to rack code (not bag ID). Check tetris on OV rack: heavy at bottom, SLAM labels visible, no overhang. Audit bag closure before P&S."))

            # 7. Bag management
            if conceded_pct > 50:
                utr_actions.append(("🟠", "Bag Management",
                    f"Conceded rate at {conceded_pct:.0f}% — majority of PNOV conceded",
                    "Audit bag closure process: bags must be zipped before Pick & Stage. Daily Gemba L3+ on bag closure. Check for damaged bags (Mars project: weekly isolation). Verify smart stow labels are in place."))

            # 8. SWA
            if swa_pct > 5:
                utr_actions.append(("🟡", "SWA",
                    f"SWA rate at {swa_pct:.1f}%",
                    "Investigate stow without assignment. Check if parcels are being stowed to wrong location or without proper scan. Audit Scanless Stow sensor positioning."))

            # 9. High Value parcels
            if hv_300 >= 3:
                utr_actions.append(("🟠", "High Value",
                    f"{hv_300} parcels with value >300€ (total €{sum(v for v in values if v >= 300):,.0f})",
                    "High-value parcels require extra attention. Ensure these are tracked through PS if missing. Cross-check stow operators handling HV — are top contributors also stowing HV? If yes, prioritize coaching on those operators."))
            elif hv_100 >= 5:
                utr_actions.append(("🟡", "High Value",
                    f"{hv_100} parcels with value >100€ (total €{hv_total_value:,.0f})",
                    "Monitor HV parcel handling. Verify stow operators for these parcels are following standard checklist. Flag if same operators appear in top contributors."))

            # 10. Split rate (UTR angle)
            if split / n * 100 > 50:
                utr_actions.append(("🟠", "Split Rate",
                    f"Split rate at {split/n*100:.0f}% — very high, parcels harder to trace",
                    f"PSUA at {psua_pct:.0f}%. High split = higher risk of parcels being left behind during transfer. Audit pick & stage process for split bags. Ensure 1-piece flow on split parcels."))

            # 11. Low recovery
            if rts_pct < 10 and return_d1 / n * 100 < 5 and conceded_pct > 30:
                utr_actions.append(("🟡", "Low Recovery",
                    f"Low recovery: RTS {rts_pct:.0f}%, D+1 {return_d1/n*100:.0f}% — with {conceded_pct:.0f}% concession",
                    "Activate PM live monitoring: each PNOV must trigger immediate investigation (don't wait until end of shift). Improve PS intervention rate. If parcels are not being recovered, they may never have been loaded."))

            # ─── OTR ROOT CAUSE ANALYSIS & ACTIONS ───

            for node in nodes_in_group:
                node_data_otr = [r for r in ms_data if r.get("station", "") == node]
                if not node_data_otr:
                    continue
                nn = len(node_data_otr)
                dists = [self._sf(r.get("distance", 0)) for r in node_data_otr if r.get("distance")]
                o100_n = len([d for d in dists if d >= 100])
                u25_n = len([d for d in dists if d < 25])
                o100_pct_node = o100_n / max(len(dists), 1) * 100
                u25_pct_node = u25_n / max(len(dists), 1) * 100

                node_conceded = len([r for r in node_data_otr if r.get("is_conceded", "").lower() in ("true", "1", "yes")])
                node_conceded_pct = node_conceded / nn * 100

                node_locker = len([r for r in node_data_otr if r.get("is_locker", "").lower() in ("true", "1", "yes")])
                node_locker_pct = node_locker / nn * 100

                node_split = len([r for r in node_data_otr if r.get("split_type", "").lower() not in ("", "none", "non-split")])
                node_split_pct = node_split / nn * 100

                node_type = "XPT" if node in XPT_TO_MS else "MS"

                # Distance analysis
                if o100_pct_node > 40:
                    otr_actions.append(("🔴", f"{node} ({node_type}) — Distance",
                        f"{o100_pct_node:.0f}% marked missing >100m from delivery point — potential DA abuse",
                        f"OTR team to request action plan from DSP. Focus on PNOV marked >100m before end of delivery tour. Schedule ride along on worst routes. If pattern confirms abuse, escalate via Performance Management policy."))
                elif o100_pct_node > 20:
                    otr_actions.append(("🟠", f"{node} ({node_type}) — Distance",
                        f"{o100_pct_node:.0f}% marked missing >100m",
                        "Review with DSP dispatcher. Verify DA is marking missing at delivery point (not bulk-marking at end of tour). Share standard: mark missing after searching van at each stop."))

                if u25_pct_node > 60:
                    otr_actions.append(("🟡", f"{node} ({node_type}) — Distance",
                        f"{u25_pct_node:.0f}% marked missing <25m — likely genuine missing",
                        "Focus UTR investigation: parcels likely never loaded. Cross-check with stow operators and bag closure audit."))

                # Top DAs
                da_otr = {}
                for r in node_data_otr:
                    da = r.get("da who marked as missing", "") or r.get("Assigned DA", "")
                    if da:
                        if da not in da_otr:
                            da_otr[da] = {"count": 0, "o100": 0, "conceded": 0}
                        da_otr[da]["count"] += 1
                        if self._sf(r.get("distance", 0)) >= 100:
                            da_otr[da]["o100"] += 1
                        if r.get("is_conceded", "").lower() in ("true", "1", "yes"):
                            da_otr[da]["conceded"] += 1

                for da, stats in sorted(da_otr.items(), key=lambda x: -x[1]["count"])[:3]:
                    cnt = stats["count"]
                    o100_da = stats["o100"]
                    if cnt >= 15:
                        if o100_da / cnt > 0.8:
                            otr_actions.append(("🔴", f"{node} — DA Outlier",
                                f"DA {da} has {cnt} PNOV ({o100_da} at >100m) — high suspicion of abuse/theft",
                                "Immediate DSP escalation. Ride along mandatory next shift. If confirmed pattern >100m: DSP to remove DA from route. Escalate via BOT/Pokemon for formal action."))
                        else:
                            otr_actions.append(("🔴", f"{node} — DA Outlier",
                                f"DA {da} has {cnt} PNOV — top offender",
                                "Immediate coaching + ride along. DSP dispatcher to investigate each missing scan. Check if DA is handling split routes correctly (1 parcel at a time, not bag by bag)."))
                    elif cnt >= 8:
                        otr_actions.append(("🟠", f"{node} — DA Outlier",
                            f"DA {da} has {cnt} PNOV",
                            "Schedule ride along on this DA's route. DSP roundtable: present data and request investigation. Share best practice: scan packages 1 by 1 on split routes."))

                # Top DSPs
                dsp_otr = {}
                for r in node_data_otr:
                    dsp = r.get("dsp", "")
                    if dsp:
                        dsp_otr[dsp] = dsp_otr.get(dsp, 0) + 1
                top_dsps = sorted(dsp_otr.items(), key=lambda x: -x[1])[:3]
                for dsp, cnt in top_dsps:
                    if cnt >= 10:
                        otr_actions.append(("🟠", f"{node} — DSP Outlier",
                            f"DSP {dsp} accounts for {cnt} PNOV ({cnt/nn*100:.0f}% of node)",
                            f"Include in next DSP roundtable. Request DSP action plan within 48h. Focus areas: DA coaching, van check at debrief, split route handling. Track WoW improvement."))

                # Concentrated routes
                route_map = {}
                for r in node_data_otr:
                    rt = r.get("route_id", "")
                    if rt:
                        route_map[rt] = route_map.get(rt, 0) + 1
                for rt, cnt in sorted(route_map.items(), key=lambda x: -x[1])[:2]:
                    if cnt >= 10:
                        otr_actions.append(("🟠", f"{node} — Route",
                            f"Route {rt} has {cnt} PNOV concentrated on single route",
                            "Investigate route plan: was there a rescue? Did rescue DA scan bags instead of individual packages? Cross-check with FSAF compliance of the stower assigned to this route's bags."))

                # Split routes
                if node_split_pct > 30:
                    otr_actions.append(("🟡", f"{node} — Split",
                        f"Split rate at {node_split_pct:.0f}% — elevated split route risk",
                        "Share with bottom DSPs via roundtable: on split routes, DA must handle parcels 1 by 1 (not bag by bag). Monitor WoW."))

                # Locker
                if node_locker_pct > 8:
                    otr_actions.append(("🟡", f"{node} — Locker",
                        f"Locker PNOV at {node_locker_pct:.1f}%",
                        "Identify recurring locker locations. If same locker recurring: open SIM ticket. Escalate locker malfunction to OpsTechIT Locker Chat. Check if DA is correctly using locker (scan at locker, not in van)."))

                # XPT specific
                if node_type == "XPT":
                    if nn >= 5:
                        otr_actions.append(("🟡", f"{node} — XPT",
                            f"XPT node {node} has {nn} PNOV — verify debrief compliance",
                            "Ensure PIT Stop debrief (Step 7 SOP) is applied. Verify TC + Dolphin access at node. DSP must attend debrief and check van content before DA leaves."))

            # Build action plan HTML — separated UTR / OTR
            findings_html = ""
            if utr_actions or otr_actions:
                findings_html = "<div style='margin-top:15px'>"

                if utr_actions:
                    findings_html += """<h3>📦 Action Plan — UTR</h3>
                    <table class='findings-table'><thead><tr>
                    <th style='width:30px'>Prio</th><th style='width:120px'>Root Cause</th><th>Finding</th><th>Recommended Action</th>
                    </tr></thead><tbody>"""
                    for prio, root_cause, finding, action in utr_actions:
                        bg = "background:#fef2f2;" if prio == "🔴" else "background:#fffbeb;" if prio == "🟠" else ""
                        findings_html += f"<tr style='{bg}'><td style='font-size:16px;text-align:center'>{prio}</td><td><b>{root_cause}</b></td><td>{finding}</td><td>{action}</td></tr>"
                    findings_html += "</tbody></table>"

                if otr_actions:
                    findings_html += """<h3>🚚 Action Plan — OTR</h3>
                    <table class='findings-table'><thead><tr>
                    <th style='width:30px'>Prio</th><th style='width:140px'>Root Cause</th><th>Finding</th><th>Recommended Action</th>
                    </tr></thead><tbody>"""
                    for prio, root_cause, finding, action in otr_actions:
                        bg = "background:#fef2f2;" if prio == "🔴" else "background:#fffbeb;" if prio == "🟠" else ""
                        findings_html += f"<tr style='{bg}'><td style='font-size:16px;text-align:center'>{prio}</td><td><b>{root_cause}</b></td><td>{finding}</td><td>{action}</td></tr>"
                    findings_html += "</tbody></table>"

                findings_html += "</div>"

            sections_html += f"""
            {findings_html}
            </div>
            <hr style="margin:30px 0;border-color:#e5e7eb">
            """

        # Final HTML
        html = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<title>PNOV Deep Dive W{self.week}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Segoe UI',Arial,sans-serif;margin:20px auto;max-width:1400px;background:#f5f5f5;color:#333}}
h1{{color:#232f3e;font-size:22px;margin-bottom:5px}}
h2{{color:#fff;font-size:15px;padding:10px 14px;background:#232f3e;border-radius:6px;margin:20px 0 12px}}
h3{{color:#232f3e;font-size:13px;margin:15px 0 8px;border-bottom:1px solid #e5e7eb;padding-bottom:4px}}
.meta{{color:#666;font-size:13px;margin-bottom:20px}}
.station-block{{background:#fff;padding:18px;border-radius:8px;margin-bottom:20px;box-shadow:0 1px 3px rgba(0,0,0,.1)}}
.stats{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:15px}}
.sc{{background:#232f3e;color:#fff;padding:8px 14px;border-radius:6px;text-align:center;min-width:80px;cursor:help;position:relative}}
.sc:hover::after{{content:attr(title);position:absolute;bottom:110%;left:50%;transform:translateX(-50%);background:#333;color:#fff;padding:6px 10px;border-radius:4px;font-size:10px;white-space:nowrap;z-index:10;opacity:0.95}}
.sc.red{{background:#dc2626}}.sc.green{{background:#16a34a}}
.sc .l{{font-size:10px;opacity:.8}}.sc .v{{font-size:16px;font-weight:700}}
table{{border-collapse:collapse;width:100%;margin-bottom:12px;font-size:12px}}
.findings-table{{margin:15px 0}}
.findings-table td{{padding:8px 10px;vertical-align:top}}
.findings-table td:first-child{{font-size:16px;text-align:center;width:30px}}
.findings-table td:nth-child(2){{width:40px}}
th{{background:#374151;color:#fff;padding:6px 10px;text-align:left;font-size:11px}}
td{{padding:5px 10px;border-bottom:1px solid #e5e7eb}}
tr:hover{{background:#f0f4ff}}
</style></head><body>
<h1>PNOV Deep Dive — W{self.week}</h1>
<div class="meta">Generated {now_str} | {len(self.selected_stations)} stations | {total} total parcels</div>
{sections_html}
</body></html>"""
        return html


# ============================================================
# GUI
# ============================================================
class PNOVApp:
    def __init__(self, root):
        self.root = root
        self.root.title("PNOV Deep Dive")
        self.root.geometry("900x650")
        self.root.configure(bg="#f5f5f5")
        self.running = False
        self.last_report = None
        self._build_ui()

    def _build_ui(self):
        # Header
        header = tk.Frame(self.root, bg="#232f3e", height=55)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(header, text="📊 PNOV Deep Dive — Automated Analysis",
                 font=("Segoe UI", 15, "bold"), bg="#232f3e", fg="white").pack(side=tk.LEFT, padx=20, pady=12)

        # Controls row
        ctrl = tk.Frame(self.root, bg="#f5f5f5", pady=10)
        ctrl.pack(fill=tk.X, padx=20)

        tk.Label(ctrl, text="Week:", font=("Segoe UI", 11, "bold"), bg="#f5f5f5").pack(side=tk.LEFT)
        now = datetime.now()
        wk = now.isocalendar()[1] - 1
        self.week_var = tk.StringVar(value=f"{now.year}-{wk:02d}")
        tk.Entry(ctrl, textvariable=self.week_var, width=9, font=("Segoe UI", 11)).pack(side=tk.LEFT, padx=(5, 15))

        # Buttons
        self.run_btn = tk.Button(ctrl, text="▶ Run", font=("Segoe UI", 11, "bold"),
                                  bg="#16a34a", fg="white", relief="flat", padx=18, pady=4,
                                  command=self.start)
        self.run_btn.pack(side=tk.LEFT, padx=(0, 8))

        self.open_btn = tk.Button(ctrl, text="📄 Open", font=("Segoe UI", 10),
                                   bg="#2563eb", fg="white", relief="flat", padx=12, pady=4,
                                   command=self.open_report, state=tk.DISABLED)
        self.open_btn.pack(side=tk.LEFT, padx=(0, 8))

        self.status_var = tk.StringVar(value="Select stations and click Run")
        tk.Label(ctrl, textvariable=self.status_var, font=("Segoe UI", 10),
                 bg="#f5f5f5", fg="#666").pack(side=tk.LEFT, padx=10)

        # Station selector — combobox + free text entry
        station_frame = tk.LabelFrame(self.root, text="Stations",
                                       font=("Segoe UI", 10, "bold"), bg="#f5f5f5", padx=10, pady=5)
        station_frame.pack(fill=tk.X, padx=20, pady=(5, 5))

        tk.Label(station_frame, text="Type or select stations (comma-separated):",
                 font=("Segoe UI", 9), bg="#f5f5f5", fg="#666").pack(anchor="w")

        input_row = tk.Frame(station_frame, bg="#f5f5f5")
        input_row.pack(fill=tk.X, pady=(4, 4))

        self.station_entry = ttk.Combobox(input_row, values=ALL_MOTHER_STATIONS,
                                           font=("Segoe UI", 11), width=50)
        self.station_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self.station_entry.set("")
        self.station_entry.bind("<<ComboboxSelected>>", self._on_combo_select)

        tk.Button(input_row, text="+ Add", font=("Segoe UI", 9, "bold"),
                  bg="#232f3e", fg="white", relief="flat", padx=10,
                  command=self._add_station).pack(side=tk.LEFT, padx=(0, 5))
        tk.Button(input_row, text="Clear", font=("Segoe UI", 9),
                  command=self._clear_stations).pack(side=tk.LEFT)

        # Selected stations display
        self.selected_label = tk.Label(station_frame, text="Selected: (none)",
                                        font=("Segoe UI", 10), bg="#f5f5f5", fg="#232f3e",
                                        wraplength=700, justify="left")
        self.selected_label.pack(anchor="w", pady=(2, 0))

        self.selected_stations_list = []

        # Progress
        self.progress = ttk.Progressbar(self.root, mode="indeterminate", length=660)
        self.progress.pack(padx=20, pady=(5, 5))

        # Log
        log_frame = tk.Frame(self.root, bg="#f5f5f5")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 10))
        self.log_text = scrolledtext.ScrolledText(log_frame, height=10, font=("Consolas", 9),
                                                   bg="#1e1e1e", fg="#d4d4d4", wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def _on_combo_select(self, event):
        self._add_station()

    def _add_station(self):
        text = self.station_entry.get().strip().upper()
        if not text:
            return
        # Support comma-separated input
        new_stations = [s.strip().upper() for s in text.split(",") if s.strip()]
        for s in new_stations:
            if s and s not in self.selected_stations_list:
                self.selected_stations_list.append(s)
        self.station_entry.set("")
        self._update_selected_label()

    def _clear_stations(self):
        self.selected_stations_list = []
        self._update_selected_label()

    def _update_selected_label(self):
        if self.selected_stations_list:
            # Show XPTs that will be included
            display = []
            for ms in self.selected_stations_list:
                xpts = MS_XPT_MAP.get(ms, [])
                if xpts:
                    display.append(f"{ms} (+{','.join(xpts)})")
                else:
                    display.append(ms)
            self.selected_label.config(text="Selected: " + " | ".join(display))
        else:
            self.selected_label.config(text="Selected: (none)")

    def log(self, msg):
        self.root.after(0, lambda: (self.log_text.insert(tk.END, msg + "\n"), self.log_text.see(tk.END)))

    def start(self):
        selected = self.selected_stations_list[:]
        if not selected:
            self.status_var.set("⚠️ Add at least one station!")
            return
        if self.running:
            return
        self.running = True
        self.run_btn.configure(state=tk.DISABLED, bg="#999")
        self.status_var.set("Running...")
        self.progress.start(15)
        self.log_text.delete("1.0", tk.END)
        threading.Thread(target=self._run, args=(selected,), daemon=True).start()

    def _run(self, selected):
        try:
            scraper = PNOVScraper(log_func=self.log, week=self.week_var.get().strip(),
                                   selected_stations=selected)
            self.last_report = scraper.run()
            self.root.after(0, self._done)
        except Exception as e:
            import traceback
            self.log(traceback.format_exc())
            self.root.after(0, lambda: self._error(str(e)))

    def _done(self):
        self.running = False
        self.progress.stop()
        self.run_btn.configure(state=tk.NORMAL, bg="#16a34a")
        self.open_btn.configure(state=tk.NORMAL)
        self.status_var.set("✅ Done!")

    def _error(self, msg):
        self.running = False
        self.progress.stop()
        self.run_btn.configure(state=tk.NORMAL, bg="#16a34a")
        self.status_var.set(f"❌ Error")

    def open_report(self):
        if self.last_report and os.path.exists(self.last_report):
            webbrowser.open(f"file:///{self.last_report}")


def main():
    root = tk.Tk()
    PNOVApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
