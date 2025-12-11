from __future__ import annotations
import sys
import json
from pathlib import Path
import io
from contextlib import redirect_stdout, redirect_stderr

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import rules_engine as engine


# ==========================
#   MODE DEV / PROD
# ==========================
# Mets DEV_MODE = True pour activer la source "Custom (invoices_to_check.csv)"
DEV_MODE = True


def load_rules():
    print("[CLIENT] Loading rules from rules_out/…", file=sys.stderr)
    rules = engine.load_all_rules()
    print(f"[CLIENT] {len(rules)} rules loaded.", file=sys.stderr)
    return rules


def filter_rules(rules, selected_codes: list[str] | None):
    """
    Limite les règles à un sous-ensemble de codes (flags).
    selected_codes: liste de codes (ex: ["CMC","BB"])
    """
    if not selected_codes:
        return rules

    wanted = {c.strip().upper() for c in selected_codes if c.strip()}
    if not wanted:
        return rules

    filtered = []
    for r in rules:
        code = getattr(r, "_rule_code", r.__name__).upper()
        if code in wanted:
            filtered.append(r)

    print(f"[CLIENT] Filtered rules: {len(filtered)} kept over {len(rules)}", file=sys.stderr)
    if not filtered:
        print("[CLIENT] WARNING: No rules match the requested codes.", file=sys.stderr)

    return filtered


def run_gui():
    root = tk.Tk()
    root.title("Invoice Compliance Client")

    # --------------------------------------------------
    # Chargement backend
    # --------------------------------------------------
    try:
        rules = load_rules()
        utbms_lookup = engine.load_utbms_lookup()
        global_lines = engine.load_global_invoice_lines(utbms_lookup)
    except Exception as e:
        messagebox.showerror("Error", f"Failed to initialize client:\n{e}")
        root.destroy()
        return

    if not global_lines:
        messagebox.showwarning("No Data", "No global invoice lines found in data/invoices.csv.")
        root.destroy()
        return

    # Groupement par facture (dataset global)
    global_invoices_map = engine.group_lines_by_invoice(global_lines)

    # Dataset custom (invoices_to_check.csv) — seulement en DEV
    custom_lines: list[dict] = []
    custom_invoices_map: dict[str, list[dict]] = {}

    if DEV_MODE:
        try:
            # On récupère le chemin par défaut depuis rules_engine si possible
            DEFAULT_CSV = getattr(engine, "DEFAULT_CSV", None)
            if DEFAULT_CSV is None:
                # fallback : même logique que dans rules_engine
                ROOT = Path(__file__).resolve().parent
                DEFAULT_CSV = ROOT / "invoices_to_check.csv"
            else:
                DEFAULT_CSV = Path(DEFAULT_CSV)

            if DEFAULT_CSV.exists():
                print(f"[CLIENT] Loading custom invoice lines from {DEFAULT_CSV}…", file=sys.stderr)
                custom_lines = engine.load_lines_from_csv(DEFAULT_CSV, utbms_lookup)
                custom_invoices_map = engine.group_lines_by_invoice(custom_lines)
                print(f"[CLIENT] Loaded {len(custom_lines)} custom lines.", file=sys.stderr)
            else:
                print(f"[CLIENT] No invoices_to_check.csv found at {DEFAULT_CSV} (dev custom disabled).", file=sys.stderr)
        except Exception as e:
            print(f"[CLIENT] Failed to load custom invoices_to_check.csv: {e}", file=sys.stderr)
            custom_lines = []
            custom_invoices_map = {}

    # Infos règles (pour affichage flags)
    rules_info = []
    for r in rules:
        code = getattr(r, "_rule_code", r.__name__)
        desc = getattr(r, "_rule_desc", "")
        version = getattr(r, "_rule_version", "")
        penalty = getattr(r, "_rule_penalty", "")
        rules_info.append({
            "code": code,
            "desc": desc,
            "version": version,
            "penalty": penalty,
            "func": r,
        })

    # Pour mémoriser les derniers résultats (Save JSON + Debug)
    last_results = {
        "flat": None,
        "nested": None,
        "sample_lines": None,
        "source": None,
    }

    # Source courante : "global" ou "custom" (si dispo)
    source_var = tk.StringVar(value="global")

    def current_dataset():
        """
        Retourne (lines, invoices_map, label_source)
        selon la source sélectionnée.
        """
        src = source_var.get()
        if src == "custom" and DEV_MODE and custom_lines:
            return custom_lines, custom_invoices_map, "custom"
        return global_lines, global_invoices_map, "global"

    # --------------------------------------------------
    # Layout principal
    # --------------------------------------------------
    root.columnconfigure(0, weight=1)
    root.columnconfigure(1, weight=1)
    root.columnconfigure(2, weight=1)
    root.rowconfigure(2, weight=1)

    title_lbl = ttk.Label(root, text="Invoice Compliance Client", font=("Segoe UI", 14, "bold"))
    title_lbl.grid(row=0, column=0, columnspan=3, pady=8)

    # --- FRAME MODE (global / custom) uniquement si DEV && custom_lines ---
    if DEV_MODE and custom_lines:
        mode_frame = ttk.Frame(root, padding=(5, 0))
        mode_frame.grid(row=1, column=0, columnspan=3, sticky="w")

        ttk.Label(mode_frame, text="Data source:").grid(row=0, column=0, padx=(0, 5))
        rb_global = ttk.Radiobutton(mode_frame, text="Global (data/invoices.csv)", value="global", variable=source_var)
        rb_global.grid(row=0, column=1, padx=(0, 5))
        rb_custom = ttk.Radiobutton(mode_frame, text="Custom (invoices_to_check.csv)", value="custom", variable=source_var)
        rb_custom.grid(row=0, column=2, padx=(0, 5))
    else:
        # occupe quand même la ligne pour la grille
        spacer = ttk.Frame(root, height=5)
        spacer.grid(row=1, column=0, columnspan=3, sticky="ew")

    # --- Frame gauche: Invoices ---
    frame_inv = ttk.Frame(root, padding=5)
    frame_inv.grid(row=2, column=0, sticky="nsew")
    frame_inv.rowconfigure(1, weight=1)
    frame_inv.columnconfigure(0, weight=1)

    lbl_invoices = ttk.Label(frame_inv, text="Invoices (INVOICE_NUMBER)")
    lbl_invoices.grid(row=0, column=0, sticky="w")

    invoices_listbox = tk.Listbox(frame_inv, selectmode=tk.EXTENDED, exportselection=False)
    invoices_listbox.grid(row=1, column=0, sticky="nsew")
    inv_scroll = ttk.Scrollbar(frame_inv, orient="vertical", command=invoices_listbox.yview)
    inv_scroll.grid(row=1, column=1, sticky="ns")
    invoices_listbox.configure(yscrollcommand=inv_scroll.set)

    # --- Frame centre: KIDs ---
    frame_kid = ttk.Frame(root, padding=5)
    frame_kid.grid(row=2, column=1, sticky="nsew")
    frame_kid.rowconfigure(1, weight=1)
    frame_kid.columnconfigure(0, weight=1)

    ttk.Label(frame_kid, text="Lines (KID) for selected invoice(s)").grid(row=0, column=0, sticky="w")

    kids_listbox = tk.Listbox(frame_kid, selectmode=tk.EXTENDED, exportselection=False)
    kids_listbox.grid(row=1, column=0, sticky="nsew")
    kid_scroll = ttk.Scrollbar(frame_kid, orient="vertical", command=kids_listbox.yview)
    kid_scroll.grid(row=1, column=1, sticky="ns")
    kids_listbox.configure(yscrollcommand=kid_scroll.set)

    # --- Frame droite: Flags / Rules ---
    frame_flags = ttk.Frame(root, padding=5)
    frame_flags.grid(row=2, column=2, sticky="nsew")
    frame_flags.rowconfigure(1, weight=1)
    frame_flags.columnconfigure(0, weight=1)

    ttk.Label(frame_flags, text="Flags / Rules").grid(row=0, column=0, sticky="w")

    flags_canvas = tk.Canvas(frame_flags, borderwidth=0)
    flags_canvas.grid(row=1, column=0, sticky="nsew")
    flags_scroll = ttk.Scrollbar(frame_flags, orient="vertical", command=flags_canvas.yview)
    flags_scroll.grid(row=1, column=1, sticky="ns")
    flags_canvas.configure(yscrollcommand=flags_scroll.set)

    flags_inner = ttk.Frame(flags_canvas)
    flags_canvas.create_window((0, 0), window=flags_inner, anchor="nw")

    flags_vars: dict[str, tk.BooleanVar] = {}

    for info in rules_info:
        code = info["code"]
        penalty = info["penalty"]
        var = tk.BooleanVar(value=False)
        flags_vars[code] = var
        text = f"{code} (pen={penalty})"
        cb = ttk.Checkbutton(flags_inner, text=text, variable=var)
        cb.pack(anchor="w", padx=2, pady=1)

    def _on_flags_configure(event):
        flags_canvas.configure(scrollregion=flags_canvas.bbox("all"))

    flags_inner.bind("<Configure>", _on_flags_configure)

    # --- Frame bas: actions + Notebook (Results / Logs / Debug) ---
    frame_bottom = ttk.Frame(root, padding=5)
    frame_bottom.grid(row=3, column=0, columnspan=3, sticky="nsew")
    frame_bottom.rowconfigure(1, weight=1)
    frame_bottom.columnconfigure(0, weight=1)

    buttons_frame = ttk.Frame(frame_bottom)
    buttons_frame.grid(row=0, column=0, sticky="w", pady=(0, 5))

    run_btn = ttk.Button(buttons_frame, text="Run rules", width=15)
    run_btn.grid(row=0, column=0, padx=(0, 5))

    save_btn = ttk.Button(buttons_frame, text="Save JSON", width=15)
    save_btn.grid(row=0, column=1, padx=(0, 5))

    clear_btn = ttk.Button(buttons_frame, text="Clear output", width=15)
    clear_btn.grid(row=0, column=2, padx=(0, 5))

    notebook = ttk.Notebook(frame_bottom)
    notebook.grid(row=1, column=0, sticky="nsew")
    frame_bottom.rowconfigure(1, weight=1)

    # Onglet Results
    frame_results = ttk.Frame(notebook)
    notebook.add(frame_results, text="Results")
    frame_results.rowconfigure(0, weight=1)
    frame_results.columnconfigure(0, weight=1)

    results_text = tk.Text(frame_results, height=12, wrap="none")
    results_text.grid(row=0, column=0, sticky="nsew")
    res_scroll_y = ttk.Scrollbar(frame_results, orient="vertical", command=results_text.yview)
    res_scroll_y.grid(row=0, column=1, sticky="ns")
    results_text.configure(yscrollcommand=res_scroll_y.set)

    # Onglet Logs
    frame_logs = ttk.Frame(notebook)
    notebook.add(frame_logs, text="Logs")
    frame_logs.rowconfigure(0, weight=1)
    frame_logs.columnconfigure(0, weight=1)

    logs_text = tk.Text(frame_logs, height=12, wrap="none")
    logs_text.grid(row=0, column=0, sticky="nsew")
    logs_scroll_y = ttk.Scrollbar(frame_logs, orient="vertical", command=logs_text.yview)
    logs_scroll_y.grid(row=0, column=1, sticky="ns")
    logs_text.configure(yscrollcommand=logs_scroll_y.set)

    # Onglet Debug
    frame_debug = ttk.Frame(notebook)
    notebook.add(frame_debug, text="Debug")
    frame_debug.rowconfigure(0, weight=1)
    frame_debug.columnconfigure(0, weight=1)

    debug_text = tk.Text(frame_debug, height=12, wrap="none")
    debug_text.grid(row=0, column=0, sticky="nsew")
    dbg_scroll_y = ttk.Scrollbar(frame_debug, orient="vertical", command=debug_text.yview)
    dbg_scroll_y.grid(row=0, column=1, sticky="ns")
    debug_text.configure(yscrollcommand=dbg_scroll_y.set)

    # Utilitaires texte
    def append_logs(text: str):
        if not text:
            return
        logs_text.insert("end", text)
        logs_text.see("end")

    # --------------------------------------------------
    # Logique de mise à jour des listes
    # --------------------------------------------------
    def refresh_invoices_listbox(*_args):
        """
        Remplit la liste des factures en fonction de la source (global/custom).
        """
        invoices_listbox.delete(0, tk.END)
        lines, inv_map, src = current_dataset()

        lbl_text = "Invoices (INVOICE_NUMBER)"
        if src == "custom":
            lbl_text += "  [CUSTOM]"
        lbl_invoices.configure(text=lbl_text)

        # inv_map : invoice_number -> list[lines]
        for inv_no, lines_for_inv in sorted(inv_map.items(), key=lambda kv: kv[0]):
            label = f"{inv_no}  ({len(lines_for_inv)} lines)"
            invoices_listbox.insert(tk.END, label)

        # On vide aussi les KIDs
        kids_listbox.delete(0, tk.END)

    def update_kids_listbox(event=None):
        """
        Remplit la liste des KID en fonction des factures sélectionnées et de la source.
        """
        kids_listbox.delete(0, tk.END)

        lines, inv_map, src = current_dataset()

        selected_indices = list(invoices_listbox.curselection())
        if not selected_indices:
            return

        selected_invoices = []
        for idx in selected_indices:
            label = invoices_listbox.get(idx)
            inv_no = label.split()[0]
            selected_invoices.append(inv_no)

        seen_kids = set()
        for inv_no in selected_invoices:
            lines_for_inv = inv_map.get(inv_no, [])
            for ln in lines_for_inv:
                kid = str(ln.get("KID") or "").strip()
                desc = str(ln.get("LINE_ITEM_DESCRIPTION") or "").strip()
                if not kid:
                    continue
                if kid in seen_kids:
                    continue
                seen_kids.add(kid)
                display = f"{kid} | {inv_no} | {desc[:60]}"
                kids_listbox.insert(tk.END, display)

    invoices_listbox.bind("<<ListboxSelect>>", update_kids_listbox)

    # Si on change de source (global/custom), on refresh tout
    def on_source_changed(*_args):
        refresh_invoices_listbox()

    source_var.trace_add("write", on_source_changed)

    # Premier remplissage
    refresh_invoices_listbox()

    # --------------------------------------------------
    # Sélection des règles et des lignes à vérifier
    # --------------------------------------------------
    def get_selected_rules():
        selected_codes = [code for code, var in flags_vars.items() if var.get()]
        if not selected_codes:
            return rules  # aucune sélection -> toutes les règles
        return filter_rules(rules, selected_codes)

    def get_selected_sample_lines():
        """
        Retourne les lignes à vérifier, en fonction de :
          - la source (global/custom)
          - la sélection d'invoices
          - la sélection de KID

        Logique :
          - si des KID sont sélectionnés : on vérifie uniquement ces KID
          - sinon, si des factures sont sélectionnées : toutes les lignes de ces factures
          - sinon : toutes les lignes de la source courante
        """
        lines, inv_map, src = current_dataset()

        # Factures sélectionnées
        selected_invoices = set()
        for idx in invoices_listbox.curselection():
            label = invoices_listbox.get(idx)
            inv_no = label.split()[0]
            selected_invoices.add(inv_no)

        # KIDs sélectionnés
        selected_kids = set()
        for idx in kids_listbox.curselection():
            label = kids_listbox.get(idx)
            kid = label.split("|")[0].strip()
            selected_kids.add(kid)

        sample_lines: list[dict] = []

        # 1) Si des KID sont sélectionnés → on ne garde que ces KID
        if selected_kids:
            for ln in lines:
                kid = str(ln.get("KID") or "").strip()
                if kid in selected_kids:
                    sample_lines.append(ln)
            return sample_lines, src

        # 2) Sinon, si des factures sont sélectionnées → lignes de ces factures
        if selected_invoices:
            for ln in lines:
                inv = str(ln.get("INVOICE_NUMBER") or "").strip()
                if inv in selected_invoices:
                    sample_lines.append(ln)
            return sample_lines, src

        # 3) Rien sélectionné → toutes les lignes de la source courante
        return list(lines), src

    # --------------------------------------------------
    # Run rules + mise à jour des 3 onglets
    # --------------------------------------------------
    def run_rules():
        # On nettoie les résultats ; on garde les logs accumulés
        results_text.delete("1.0", tk.END)
        debug_text.delete("1.0", tk.END)
        last_results["flat"] = None
        last_results["nested"] = None
        last_results["sample_lines"] = None
        last_results["source"] = None

        try:
            buf_out = io.StringIO()
            buf_err = io.StringIO()

            with redirect_stdout(buf_out), redirect_stderr(buf_err):
                active_rules = get_selected_rules()
                if not active_rules:
                    print("[CLIENT] WARNING: No rules selected / available.")
                    return

                sample_lines, src = get_selected_sample_lines()
                if not sample_lines:
                    print("[CLIENT] WARNING: No invoice lines selected in source:", src)
                    return

                # Contexte : on se rapproche de rules_engine
                # global_lines = toujours le dataset principal (contexte "réel")
                all_context_lines = engine.build_all_context_lines(global_lines, sample_lines)

                nested_results = engine.apply_rules_to_invoices_from_lines(
                    sample_lines,
                    active_rules,
                    all_context_lines,
                    debug=False,
                )
                flat_results = engine.flatten_results_per_line(nested_results)

            # Logs
            logs_text_run = buf_out.getvalue() + buf_err.getvalue()
            append_logs(logs_text_run)

            # Mémorisation pour Save JSON + Debug
            last_results["flat"] = flat_results
            last_results["nested"] = nested_results
            last_results["sample_lines"] = sample_lines
            last_results["source"] = src

            # ---- Onglet Results (vue lisible) ----
            if not flat_results:
                results_text.insert("1.0", "No flags triggered for selected lines.\n")
            else:
                lines_out: list[str] = []
                for line_result in flat_results:
                    kid = line_result.get("KID")
                    inv = line_result.get("INVOICE_NUMBER")
                    lf_matter = line_result.get("LAW_FIRM_MATTER_ID")
                    flags = line_result.get("flags", [])

                    codes = [f.get("code") for f in flags if f.get("code")]
                    if codes:
                        codes_str = ", ".join(codes)
                    else:
                        codes_str = "None"

                    header = f"KID {kid} (Invoice {inv}, Matter {lf_matter}) → Flags: [{codes_str}]"
                    lines_out.append(header)

                    for f in flags:
                        code = f.get("code")
                        msg = f.get("message")
                        lines_out.append(f"   - {code}: {msg}")

                    lines_out.append("")

                results_text.insert("1.0", "\n".join(lines_out))

            # ---- Onglet Debug ----
            dbg_lines: list[str] = []
            dbg_lines.append(f"Source used: {src}")
            dbg_lines.append("")
            dbg_lines.append("Sample lines:")
            for ln in sample_lines:
                kid = ln.get("KID")
                inv = ln.get("INVOICE_NUMBER")
                desc = (ln.get("LINE_ITEM_DESCRIPTION") or "").strip()
                dbg_lines.append(f" - KID={kid}, INV={inv}, DESC={desc[:80]}")
            dbg_lines.append("")
            dbg_lines.append("Nested results (per invoice/line):")
            try:
                dbg_lines.append(json.dumps(nested_results, indent=2, default=str))
            except Exception:
                dbg_lines.append(repr(nested_results))

            debug_text.insert("1.0", "\n".join(dbg_lines))

            # On se met automatiquement sur Results
            notebook.select(frame_results)

        except Exception as e:
            messagebox.showerror("Error", f"Error while running rules:\n{e}")

    def save_json():
        flat = last_results.get("flat")
        if flat is None:
            messagebox.showinfo("Empty", "No results to save yet.\nRun rules first.")
            return

        fp = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            title="Save JSON output"
        )
        if not fp:
            return
        try:
            with open(fp, "w", encoding="utf-8") as f:
                f.write(json.dumps(flat, indent=2) + "\n")
            messagebox.showinfo("Saved", f"JSON saved to:\n{fp}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save file:\n{e}")

    def clear_output():
        results_text.delete("1.0", tk.END)
        logs_text.delete("1.0", tk.END)
        debug_text.delete("1.0", tk.END)
        last_results["flat"] = None
        last_results["nested"] = None
        last_results["sample_lines"] = None
        last_results["source"] = None

    run_btn.configure(command=run_rules)
    save_btn.configure(command=save_json)
    clear_btn.configure(command=clear_output)

    root.minsize(1000, 650)
    root.mainloop()


if __name__ == "__main__":
    try:
        run_gui()
    except KeyboardInterrupt:
        sys.exit(130)
