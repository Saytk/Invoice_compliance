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
DEV_MODE = True


# ==========================
#   Helpers locaux
# ==========================

def filter_rules(rules, selected_codes: list[str] | None):
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

    engine.log_info(f"[CLIENT] Filtered rules: {len(filtered)} kept over {len(rules)}")
    if not filtered:
        engine.log_warn("[CLIENT] WARNING: No rules match the requested codes.")

    return filtered


def group_lines_by_invoice(lines):
    invoices = {}
    for line in lines:
        inv_no = str(line.get("INVOICE_NUMBER") or "").strip()
        if inv_no not in invoices:
            invoices[inv_no] = []
        invoices[inv_no].append(line)
    return invoices


# ==========================
#   GUI
# ==========================

def run_gui():
    root = tk.Tk()
    root.title("Invoice Compliance Client")

    # --------------------------------------------------
    # Chargement backend via init_engine()
    # --------------------------------------------------
    try:
        engine.set_log_level("DEBUG")

        # custom_csv uniquement en DEV_MODE
        custom_csv = None
        if DEV_MODE:
            default_custom_csv = getattr(engine, "DEFAULT_CSV", None)
            if default_custom_csv is None:
                default_custom_csv = Path(__file__).resolve().parent / "invoices_to_check.csv"
            else:
                default_custom_csv = Path(default_custom_csv)

            if default_custom_csv.exists():
                custom_csv = default_custom_csv
            else:
                engine.log_info(f"[CLIENT] No invoices_to_check.csv found at {default_custom_csv} (dev custom disabled).")

        ctx = engine.init_engine(custom_csv=custom_csv, log_level="INFO", debug=False)

        rules = ctx["rules"]
        global_lines = ctx["global_lines"]
        custom_lines = ctx["custom_lines"]
        all_lines = ctx["all_lines"]

    except Exception as e:
        messagebox.showerror("Error", f"Failed to initialize client:\n{e}")
        root.destroy()
        return

    if not global_lines and not custom_lines:
        messagebox.showwarning(
            "No Data",
            "No invoice lines found in data/invoices.csv or invoices_to_check.csv.",
        )
        root.destroy()
        return

    # Groupement par facture
    global_invoices_map = group_lines_by_invoice(global_lines)
    custom_invoices_map = group_lines_by_invoice(custom_lines) if custom_lines else {}

    # Infos règles (pour affichage flags)
    rules_info = []
    for r in rules:
        rules_info.append({
            "code": getattr(r, "_rule_code", r.__name__),
            "desc": getattr(r, "_rule_desc", ""),
            "version": getattr(r, "_rule_version", ""),
            "penalty": getattr(r, "_rule_penalty", None),
            "func": r,
        })

    # Derniers résultats (Save JSON + Debug)
    last_results = {"flat": None, "nested": None, "sample_lines": None, "source": None}

    # Source courante
    source_var = tk.StringVar(value="global")

    def current_dataset():
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

    # Mode global/custom
    if DEV_MODE and custom_lines:
        mode_frame = ttk.Frame(root, padding=(5, 0))
        mode_frame.grid(row=1, column=0, columnspan=3, sticky="w")

        ttk.Label(mode_frame, text="Data source:").grid(row=0, column=0, padx=(0, 5))
        ttk.Radiobutton(mode_frame, text="Global (data/invoices.csv)", value="global", variable=source_var).grid(row=0, column=1, padx=(0, 5))
        ttk.Radiobutton(mode_frame, text="Custom (invoices_to_check.csv)", value="custom", variable=source_var).grid(row=0, column=2, padx=(0, 5))
    else:
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
        code = str(info["code"])
        penalty = info["penalty"]
        var = tk.BooleanVar(value=False)
        flags_vars[code] = var

        pen_txt = ""
        if penalty not in (None, "", "None"):
            pen_txt = f" (pen={penalty})"

        cb = ttk.Checkbutton(flags_inner, text=f"{code}{pen_txt}", variable=var)
        cb.pack(anchor="w", padx=2, pady=1)

    def _on_flags_configure(_event):
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

    # Results
    frame_results = ttk.Frame(notebook)
    notebook.add(frame_results, text="Results")
    frame_results.rowconfigure(0, weight=1)
    frame_results.columnconfigure(0, weight=1)

    results_text = tk.Text(frame_results, height=12, wrap="none")
    results_text.grid(row=0, column=0, sticky="nsew")
    ttk.Scrollbar(frame_results, orient="vertical", command=results_text.yview).grid(row=0, column=1, sticky="ns")

    # Logs
    frame_logs = ttk.Frame(notebook)
    notebook.add(frame_logs, text="Logs")
    frame_logs.rowconfigure(0, weight=1)
    frame_logs.columnconfigure(0, weight=1)

    logs_text = tk.Text(frame_logs, height=12, wrap="none")
    logs_text.grid(row=0, column=0, sticky="nsew")
    ttk.Scrollbar(frame_logs, orient="vertical", command=logs_text.yview).grid(row=0, column=1, sticky="ns")

    # Debug
    frame_debug = ttk.Frame(notebook)
    notebook.add(frame_debug, text="Debug")
    frame_debug.rowconfigure(0, weight=1)
    frame_debug.columnconfigure(0, weight=1)

    debug_text = tk.Text(frame_debug, height=12, wrap="none")
    debug_text.grid(row=0, column=0, sticky="nsew")
    ttk.Scrollbar(frame_debug, orient="vertical", command=debug_text.yview).grid(row=0, column=1, sticky="ns")

    def append_logs(text: str):
        if not text:
            return
        logs_text.insert("end", text)
        logs_text.see("end")

    # --------------------------------------------------
    # Lists update
    # --------------------------------------------------
    def refresh_invoices_listbox(*_args):
        invoices_listbox.delete(0, tk.END)
        _, inv_map, src = current_dataset()

        lbl_text = "Invoices (INVOICE_NUMBER)"
        if src == "custom":
            lbl_text += "  [CUSTOM]"
        lbl_invoices.configure(text=lbl_text)

        for inv_no, lines_for_inv in sorted(inv_map.items(), key=lambda kv: kv[0]):
            invoices_listbox.insert(tk.END, f"{inv_no}  ({len(lines_for_inv)} lines)")

        kids_listbox.delete(0, tk.END)

    def update_kids_listbox(_event=None):
        kids_listbox.delete(0, tk.END)
        _, inv_map, _src = current_dataset()

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
            for ln in inv_map.get(inv_no, []):
                kid = str(ln.get("KID") or "").strip()
                desc = str(ln.get("LINE_ITEM_DESCRIPTION") or "").strip()
                if not kid or kid in seen_kids:
                    continue
                seen_kids.add(kid)
                kids_listbox.insert(tk.END, f"{kid} | {inv_no} | {desc[:60]}")

    invoices_listbox.bind("<<ListboxSelect>>", update_kids_listbox)

    def on_source_changed(*_args):
        refresh_invoices_listbox()

    source_var.trace_add("write", on_source_changed)
    refresh_invoices_listbox()

    # --------------------------------------------------
    # Selection helpers
    # --------------------------------------------------
    def get_selected_rules():
        selected_codes = [code for code, var in flags_vars.items() if var.get()]
        if not selected_codes:
            return rules
        return filter_rules(rules, selected_codes)

    def get_selected_sample_lines():
        lines, _inv_map, src = current_dataset()

        selected_invoices = set()
        for idx in invoices_listbox.curselection():
            inv_no = invoices_listbox.get(idx).split()[0]
            selected_invoices.add(inv_no)

        selected_kids = set()
        for idx in kids_listbox.curselection():
            kid = kids_listbox.get(idx).split("|")[0].strip()
            selected_kids.add(kid)

        if selected_kids:
            return [ln for ln in lines if str(ln.get("KID") or "").strip() in selected_kids], src

        if selected_invoices:
            return [ln for ln in lines if str(ln.get("INVOICE_NUMBER") or "").strip() in selected_invoices], src

        return list(lines), src

    # --------------------------------------------------
    # Run rules
    # --------------------------------------------------
    def run_rules():
        results_text.delete("1.0", tk.END)
        debug_text.delete("1.0", tk.END)
        last_results.update({"flat": None, "nested": None, "sample_lines": None, "source": None})

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

                flat_results = []
                for ln in sample_lines:
                    ln_with_ctx = dict(ln)
                    ln_with_ctx["ALL_LINES"] = all_lines

                    flags = engine.apply_rules_to_line(
                        ln_with_ctx,
                        active_rules,
                        debug_errors=False,
                    )

                    flat_results.append({
                        "KID": ln.get("KID"),
                        "INVOICE_NUMBER": ln.get("INVOICE_NUMBER"),
                        "LINE_ITEM_NUMBER": ln.get("LINE_ITEM_NUMBER"),
                        "LAW_FIRM_MATTER_ID": ln.get("LAW_FIRM_MATTER_ID"),
                        "flags": flags if flags else [],
                    })

                nested_results = flat_results

            append_logs(buf_out.getvalue() + buf_err.getvalue())

            last_results.update({
                "flat": flat_results,
                "nested": nested_results,
                "sample_lines": sample_lines,
                "source": src,
            })

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
                    codes_str = ", ".join(codes) if codes else "None"
                    lines_out.append(f"KID {kid} (Invoice {inv}, Matter {lf_matter}) → Flags: [{codes_str}]")

                    for f in flags:
                        lines_out.append(f"   - {f.get('code')}: {f.get('message')}")

                    lines_out.append("")

                results_text.insert("1.0", "\n".join(lines_out))

            dbg_lines: list[str] = []
            dbg_lines.append(f"Source used: {src}\n")
            dbg_lines.append("Sample lines:")
            for ln in sample_lines:
                dbg_lines.append(
                    f" - KID={ln.get('KID')}, INV={ln.get('INVOICE_NUMBER')}, DESC={(ln.get('LINE_ITEM_DESCRIPTION') or '')[:80]}"
                )
            dbg_lines.append("\nFlat / nested results (per line):")
            try:
                dbg_lines.append(json.dumps(nested_results, indent=2, default=str))
            except Exception:
                dbg_lines.append(repr(nested_results))

            debug_text.insert("1.0", "\n".join(dbg_lines))
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
        last_results.update({"flat": None, "nested": None, "sample_lines": None, "source": None})

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
