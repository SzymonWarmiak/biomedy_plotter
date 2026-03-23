import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.widgets import Button, Slider
import tkinter as tk
from tkinter import messagebox
from tkinter.scrolledtext import ScrolledText
import os
import glob
import sys
import json
import re
from scipy.signal import find_peaks, peak_widths
from scipy.integrate import simpson
from scipy.stats import linregress
import datetime

def wyznacz_krzywa_kalibracyjna(lines_info: list[dict], is_smoothed: bool) -> dict | None:
    """
    Parsuje nazwy plików pod kątem substancji i objętości, oblicza średnie pole pod największym pikiem 
    oraz jego średnią wysokość, po czym wyznacza proste kalibracyjne metodą najmniejszych kwadratów 
    dla każdej wykrytej substancji oddzielnie.
    """
    data_grouped = {}
    
    for line_dict in lines_info:
        nazwa = line_dict['nazwa']
        # Oczekiwany format: [substancja]_[objetosc][u/ul], np. etanol_08u.txt
        match = re.search(r'^([a-zA-Z0-9]+)_([\d\.,_]+)u[l]?', nazwa, re.IGNORECASE)
        if not match:
            continue
            
        substancja = match.group(1).lower()
        vol_str = match.group(2)
        
        # Zamiana separatorów ułamkowych
        vol_str = vol_str.replace('_', '.').replace(',', '.')
        
        # Edge case: np. "08" traktowane automatycznie jako "0.8"
        if vol_str.startswith('0') and '.' not in vol_str and len(vol_str) > 1:
            vol_str = vol_str[0] + '.' + vol_str[1:]
            
        try:
            volume = float(vol_str)
        except ValueError:
            continue

        y_data = np.array(line_dict['wygladzony_y'] if is_smoothed else line_dict['oryginalny_y'])
        y_data = np.nan_to_num(y_data)
        x_data = np.array(range(len(y_data)))
        
        prominence = max(0.05 * (np.max(y_data) - np.min(y_data)), 1e-6)
        peaks, _ = find_peaks(y_data, prominence=prominence)
        
        if len(peaks) == 0:
            continue
            
        # W przypadku detekcji więcej niż 1 piku w czystej próbce - wybieramy ten o najwyższej amplitudzie
        best_peak_idx = max(peaks, key=lambda p: y_data[p])
        
        results_half = peak_widths(y_data, [best_peak_idx], rel_height=0.95)
        left_idx = int(results_half[2][0])
        right_idx = int(results_half[3][0])
        
        x_slice = x_data[left_idx:right_idx+1]
        y_slice = y_data[left_idx:right_idx+1]
        
        if len(x_slice) >= 2:
            area = simpson(y_slice, x=x_slice)
            height = float(np.max(y_slice))
            
            if substancja not in data_grouped:
                data_grouped[substancja] = {}
            if volume not in data_grouped[substancja]:
                data_grouped[substancja][volume] = {"areas": [], "heights": []}
                
            data_grouped[substancja][volume]["areas"].append(area)
            data_grouped[substancja][volume]["heights"].append(height)
            
    if not data_grouped:
        return None
        
    results = {}
    for subst, volumes_data in data_grouped.items():
        if len(volumes_data) < 2:
            continue
            
        V_list = list(volumes_data.keys())
        mean_A_list = [float(np.mean(volumes_data[v]["areas"])) for v in V_list]
        mean_H_list = [float(np.mean(volumes_data[v]["heights"])) for v in V_list]
        
        slope_A, intercept_A, r_value_A, _, _ = linregress(V_list, mean_A_list)
        slope_H, intercept_H, r_value_H, _, _ = linregress(V_list, mean_H_list)
        
        results[subst] = {
            'area': (slope_A, intercept_A, r_value_A**2),
            'height': (slope_H, intercept_H, r_value_H**2)
        }
        
    return results if results else None

def wizualizuj_sygnaly(sciezki_do_plikow):
    if not sciezki_do_plikow:
        return

    fig = plt.figure(figsize=(14, 6))
    ax = plt.gca()
    
    lines_info = []
    y_min_global = float('inf')
    y_max_global = float('-inf')

    for sciezka_do_pliku in sciezki_do_plikow:
        nazwa_pliku = os.path.basename(sciezka_do_pliku)
        try:
            _, ext = os.path.splitext(sciezka_do_pliku)

            if ext.lower() == '.txt':
                # Znacznie wydajniejsze wczytywanie plików txt za pomocą Pandas
                df_txt = pd.read_csv(sciezka_do_pliku, sep=r'\s+', header=None, engine='python', on_bad_lines='skip')
                y_arr = pd.to_numeric(df_txt.values.flatten(), errors='coerce')
                y = y_arr[~pd.isna(y_arr)].tolist()
                x = list(range(len(y)))
            elif ext.lower() == '.json':
                y = []
                with open(sciezka_do_pliku, 'r', encoding='utf-8') as f:
                    dane_json = json.load(f)
                    if 'data' in dane_json:
                        for pomiar in dane_json['data']:
                            if 'ecg' in pomiar and 'Samples' in pomiar['ecg']:
                                y.extend(pomiar['ecg']['Samples'])
                x = list(range(len(y)))
            else:
                df = pd.read_excel(sciezka_do_pliku, header=0)
                # Zabezpieczenie przed błędami - dynamiczne sprawdzanie ilości kolumn
                if df.shape[1] >= 3:
                    x = df.iloc[:, 2].tolist()
                    y = df.iloc[:, 1].tolist()
                elif df.shape[1] >= 2:
                    y = df.iloc[:, 1].tolist()
                    x = list(range(len(y)))
                elif df.shape[1] >= 1:
                    y = df.iloc[:, 0].tolist()
                    x = list(range(len(y)))
                else:
                    x, y = [], []

            if len(y) == 0:
                print(f"Pominięto plik '{nazwa_pliku}': brak danych.")
                continue

            y_min = min(y)
            y_max = max(y)

            if abs(y_max - y_min) < 1e-6:
                print(f"Pominięto plik '{nazwa_pliku}': sygnał płaski (flatline).")
                continue

            y_min_global = min(y_min_global, y_min)
            y_max_global = max(y_max_global, y_max)

            window_size = 50
            y_series = pd.Series(y)
            y_smoothed = y_series.rolling(window=window_size, center=True, min_periods=1).mean().tolist()

            line, = plt.plot(x, y, linewidth=0.8, label=f'{nazwa_pliku} (Oryginał)', picker=5)

            lines_info.append({
                'nazwa': nazwa_pliku,
                'line_obj': line,
                'oryginalny_y': y,
                'y_series': y_series,
                'wygladzony_y': y_smoothed
            })

        except FileNotFoundError:
            print(f"Błąd: Nie znaleziono pliku '{nazwa_pliku}'. Sprawdź ścieżkę.")
        except Exception as e:
            print(f"Błąd podczas wczytywania '{nazwa_pliku}': {e}")

    if not lines_info:
        messagebox.showinfo("Brak Danych", "Żaden z wybranych plików nie zawiera poprawnych danych do wizualizacji.")
        plt.close()
        return

    rozpietosc = y_max_global - y_min_global
    margines = rozpietosc * 0.15
    if rozpietosc == 0: margines = 1
    plt.ylim(y_min_global - margines, y_max_global + margines)

    plt.xlabel('Numer pomiaru (LP) / Oś X', fontsize=12)
    plt.ylabel('Amplituda / Wartość', fontsize=12)
    
    plt.grid(True, which='both', linestyle='--', linewidth=0.5)
    plt.legend()
    
    plt.subplots_adjust(bottom=0.25)

    # --- Tooltip ---
    annot = ax.annotate("", xy=(0,0), xytext=(15,15), textcoords="offset points",
                        bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="gray", lw=1, alpha=0.9),
                        arrowprops=dict(arrowstyle="->", connectionstyle="arc3,rad=0"))
    annot.set_visible(False)

    def update_annot(ind, line_dict):
        line = line_dict['line_obj']
        x_data = line.get_xdata()
        y_data = line.get_ydata()
        idx = ind["ind"][0]
        pos_x, pos_y = x_data[idx], y_data[idx]
        annot.xy = (pos_x, pos_y)
        
        try:
            if isinstance(pos_x, (int, float)) and float(pos_x).is_integer():
                text = f"Plik: {line_dict['nazwa']}\nLP: {int(pos_x)}\nWartość: {float(pos_y):.4f}"
            else:
                text = f"Plik: {line_dict['nazwa']}\nOś X: {float(pos_x):.4f}\nWartość: {float(pos_y):.4f}"
        except Exception:
            text = f"Plik: {line_dict['nazwa']}\nX: {pos_x}\nY: {pos_y}"
        annot.set_text(text)

    def hover(event):
        if event.button is not None:
            return
        
        is_hovering = False
        if event.inaxes == ax:
            for line_dict in lines_info:
                line = line_dict['line_obj']
                cont, ind = line.contains(event)
                if cont:
                    update_annot(ind, line_dict)
                    annot.set_visible(True)
                    fig.canvas.draw_idle()
                    is_hovering = True
                    break
        
        if not is_hovering and annot.get_visible():
            annot.set_visible(False)
            fig.canvas.draw_idle()

    fig.canvas.mpl_connect("motion_notify_event", hover)
    # ---------------

    def zapisz_wykres(event):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(script_dir)
        screenshots_dir = os.path.join(project_root, "screeny")
        os.makedirs(screenshots_dir, exist_ok=True)

        if len(lines_info) == 1:
            base_name = os.path.splitext(lines_info[0]['nazwa'])[0]
        else:
            base_name = "Wykres_zbiorczy_" + "_".join([os.path.splitext(l['nazwa'])[0][:5] for l in lines_info])[:30]

        save_path = os.path.join(screenshots_dir, f"{base_name}.png")
        
        ax_button.set_visible(False)
        ax_smooth_button.set_visible(False)
        ax_slider.set_visible(False)
        ax_chromato.set_visible(False)
        fig.savefig(save_path)
        
        ax_button.set_visible(True)
        ax_smooth_button.set_visible(True)
        ax_slider.set_visible(True)
        ax_chromato.set_visible(True)
        messagebox.showinfo("Zapisano Wykres", f"Wykres został zapisany jako '{os.path.basename(save_path)}' w folderze 'screeny'.")

    ax_button = plt.axes([0.65, 0.03, 0.15, 0.075])
    przycisk_zapisu = Button(ax_button, 'Zapisz jako PNG')
    przycisk_zapisu.on_clicked(zapisz_wykres)

    ax_slider = plt.axes([0.12, 0.05, 0.25, 0.03])
    slider_window = Slider(ax_slider, 'Rozmiar okna', 1, 500, valinit=50, valstep=1)

    is_smoothed = False

    def update_slider(val):
        if not is_smoothed: return
        window_size = int(slider_window.val)
        y_min_curr, y_max_curr = float('inf'), float('-inf')
        
        for line_dict in lines_info:
            new_smoothed = line_dict['y_series'].rolling(window=window_size, center=True, min_periods=1).mean().tolist()
            line_dict['wygladzony_y'] = new_smoothed
            line = line_dict['line_obj']
            line.set_ydata(new_smoothed)
            line.set_label(f"{line_dict['nazwa']} (Wygładzony, okno={window_size})")
            
            y_min_curr = min(y_min_curr, min(new_smoothed))
            y_max_curr = max(y_max_curr, max(new_smoothed))
            
        rozp = y_max_curr - y_min_curr
        marg = rozp * 0.15
        if rozp == 0: marg = 1
        ax.set_ylim(y_min_curr - marg, y_max_curr + marg)
        ax.legend()
        fig.canvas.draw_idle()
        
    slider_window.on_changed(update_slider)

    def toggle_smooth(event):
        nonlocal is_smoothed
        is_smoothed = not is_smoothed
        
        if is_smoothed:
            przycisk_wygladzania.label.set_text('Przywróć oryginał')
            update_slider(slider_window.val)
        else:
            przycisk_wygladzania.label.set_text('Wygładź sygnał')
            y_min_curr, y_max_curr = float('inf'), float('-inf')
            for line_dict in lines_info:
                line = line_dict['line_obj']
                line.set_ydata(line_dict['oryginalny_y'])
                line.set_label(f"{line_dict['nazwa']} (Oryginał)")
                new_y = line_dict['oryginalny_y']
                y_min_curr = min(y_min_curr, min(new_y))
                y_max_curr = max(y_max_curr, max(new_y))
            
            rozp = y_max_curr - y_min_curr
            marg = rozp * 0.15
            if rozp == 0: marg = 1
            ax.set_ylim(y_min_curr - marg, y_max_curr + marg)
            ax.legend()
            fig.canvas.draw_idle()

    ax_smooth_button = plt.axes([0.45, 0.03, 0.15, 0.075])
    przycisk_wygladzania = Button(ax_smooth_button, 'Wygładź sygnał')
    przycisk_wygladzania.on_clicked(toggle_smooth)

    # --- NOWY MODUŁ: Analiza Chromatograficzna ---
    ax_chromato = plt.axes([0.82, 0.03, 0.15, 0.075])
    przycisk_chromato = Button(ax_chromato, 'Analiza Pików')

    def wykonaj_analize_chromatografii(event):
        # --- WSPÓŁCZYNNIKI KOREKCYJNE k_i ---
        k_factors = [1.0, 1.0, 1.0]
        
        f_log = None
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.dirname(script_dir)
            logs_dir = os.path.join(project_root, "logi_analiz")
            os.makedirs(logs_dir, exist_ok=True)
            
            now = datetime.datetime.now()
            timestamp_str = now.strftime("%Y-%m-%d_%H-%M-%S")
            log_filename = f"Analiza_{timestamp_str}.txt"
            log_filepath = os.path.join(logs_dir, log_filename)
            
            f_log = open(log_filepath, "w", encoding="utf-8")
            
            def cprint(msg=""):
                print(msg)
                if f_log: f_log.write(str(msg) + "\n")

            cprint("\n" + "="*50)
            cprint(" ROZPOCZĘCIE ANALIZY CHROMATOGRAFICZNEJ ")
            cprint(f" Data i czas: {now.strftime('%Y-%m-%d %H:%M:%S')}")
            cprint("="*50)
            
            for line_dict in lines_info:
                y_data = np.array(line_dict['wygladzony_y'] if is_smoothed else line_dict['oryginalny_y'])
                y_data = np.nan_to_num(y_data)  # Zabezpieczenie przed ewentualnymi brakami w danych (NaN)
                x_data = np.array(range(len(y_data)))
                linia_kolor = line_dict['line_obj'].get_color()

                # 1. Automatyczna detekcja pików - dodanie marginesu by prominence > 0
                prominence = max(0.05 * (np.max(y_data) - np.min(y_data)), 1e-6)
                peaks, _ = find_peaks(y_data, prominence=prominence)

                if len(peaks) == 0:
                    cprint(f"Brak wyraźnych pików dla pliku {line_dict['nazwa']}.")
                    continue

                cprint(f"\nPlik: {line_dict['nazwa']}")
                cprint(f"Znaleziono {len(peaks)} pików (czasy retencji).")

                results_half = peak_widths(y_data, peaks, rel_height=0.95)
                widths, width_heights, left_ips, right_ips = results_half

                areas = []
                handles, labels = ax.get_legend_handles_labels()
                lbl = 'Detekcja Pików' if 'Detekcja Pików' not in labels else ""
                ax.plot(x_data[peaks], y_data[peaks], "x", color=linia_kolor, markersize=8, label=lbl)

                for i in range(len(peaks)):
                    # 2. Separacja pików (zrzucenie prostopadłej pomiędzy wierzchołkami)
                    left_idx = int(left_ips[i])
                    right_idx = int(right_ips[i])
                    
                    if i > 0 and left_idx < peaks[i-1]:
                        left_idx = int((peaks[i] + peaks[i-1]) / 2)
                    if i < len(peaks) - 1 and right_idx > peaks[i+1]:
                        right_idx = int((peaks[i] + peaks[i+1]) / 2)

                    # 3. Całkowanie (Pole pod pikiem metodą Simpsona)
                    x_slice = x_data[left_idx:right_idx+1]
                    y_slice = y_data[left_idx:right_idx+1]
                    
                    # Zabezpieczenie na wypadek ekstremalnie wąskich pików
                    if len(x_slice) >= 2:
                        area = simpson(y_slice, x=x_slice)
                    else:
                        area = 0.0
                        
                    areas.append(area)
                    peak_height = y_data[peaks[i]]
                    
                    if len(x_slice) > 0:
                        ax.fill_between(x_slice, y_slice, color=linia_kolor, alpha=0.3)
                    cprint(f" Pik {i+1}: Czas retencji = {peaks[i]}, Wysokość = {peak_height:.2f}, Pole (A) = {area:.2f}")

                # 4. Normalizacja wewnętrzna
                cprint("\n--- Analiza ilościowa: Normalizacja wewnętrzna ---")
                
                corrected_areas = []
                for i, area in enumerate(areas):
                    k = k_factors[i] if i < len(k_factors) else 1.0
                    corrected_areas.append(area * k)
                    
                total_corrected_area = sum(corrected_areas)
                
                if total_corrected_area == 0:
                    cprint(" Całkowite skorygowane pole pod pikami wynosi 0, pomijam analizę stężeń.")
                else:
                    for i, area in enumerate(areas):
                        k = k_factors[i] if i < len(k_factors) else 1.0
                        corr_area = corrected_areas[i]
                        stezenie = (corr_area / total_corrected_area) * 100
                        cprint(f" Składnik {i+1}: stężenie = {stezenie:.2f}% (k={k}, Skorygowane pole: {corr_area:.2f})")

            # Wywołanie nowej funkcji poza pętlą dla plików (grupowanie i regresja globalna)
            wynik_kalibracji = wyznacz_krzywa_kalibracyjna(lines_info, is_smoothed)
            
            if wynik_kalibracji:
                for substancja, wynik in wynik_kalibracji.items():
                    cprint(f"\n--- Kalibracja bezwzględna: {substancja.upper()} ---")
                    a_A, b_A, r2_A = wynik['area']
                    a_H, b_H, r2_H = wynik['height']
                    cprint(" Na podstawie POLA POD PIKIEM (A):")
                    cprint(f"  Odpowiedź = {a_A:.4f} * V + {b_A:.4f}  |  R^2 = {r2_A:.4f}")
                    cprint(" Na podstawie WYSOKOŚCI PIKU (h):")
                    cprint(f"  Odpowiedź = {a_H:.4f} * V + {b_H:.4f}  |  R^2 = {r2_H:.4f}")
            else:
                cprint("\n--- Kalibracja bezwzględna (metoda najmniejszych kwadratów) ---")
                cprint(" Brak wystarczających danych do wyznaczenia prostej. Upewnij się,")
                cprint(" że nazwy plików wzorców są w formacie '[substancja]_[objętość]u' (np. 'etanol_08u.txt').")

            handles, labels = ax.get_legend_handles_labels()
            by_label = dict(zip(labels, handles))
            ax.legend(by_label.values(), by_label.keys())
            fig.canvas.draw_idle()
            cprint("="*50 + "\n")
            
            messagebox.showinfo("Analiza Zapisana", f"Log z analizy został utworzony w:\n{log_filename}\nw folderze 'logi_analiz'.")
            
        except Exception as e:
            print(f" Wystąpił niespodziewany błąd podczas analizy: {e}")
            print("="*50 + "\n")
        finally:
            if f_log:
                f_log.close()

    przycisk_chromato.on_clicked(wykonaj_analize_chromatografii)

    plt.show()

def uruchom_gui():
    root = tk.Tk()
    root.title("Wybór pliku EMG / GSR")
    root.geometry("500x500")

    tk.Label(root, text="Wybierz plik danych z folderu:").pack(pady=10)

    listbox = tk.Listbox(root, selectmode=tk.MULTIPLE)
    listbox.pack(expand=True, fill="both", padx=20, pady=5)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    pliki_mapa = []

    for root_dir, dirs, files in os.walk(project_root):
        # Ignoruj foldery ukryte (np. .git) oraz foldery środowisk wirtualnych
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('venv', 'env', '__pycache__')]
        
        for file in files:
            if file.lower().endswith(('.xlsx', '.xls', '.txt', '.json')):
                pelna_sciezka = os.path.join(root_dir, file)
                pliki_mapa.append(pelna_sciezka)
                listbox.insert(tk.END, os.path.relpath(pelna_sciezka, project_root))

    def on_click():
        sel = listbox.curselection()
        if not sel:
            messagebox.showwarning("Uwaga", "Zaznacz przynajmniej jeden plik z listy!")
            return
        
        wybrane_pliki = [pliki_mapa[i] for i in sel]
        wizualizuj_sygnaly(wybrane_pliki)

    tk.Button(root, text="Rysuj Wykres", command=on_click, height=2, bg="#e1e1e1").pack(pady=20)

    # --- Sekcja Logów ---
    tk.Label(root, text="Logi aplikacji:").pack(pady=(5, 0), anchor="w", padx=20)
    
    log_widget = ScrolledText(root, height=8, state='disabled', font=("Consolas", 9))
    log_widget.pack(expand=True, fill="both", padx=20, pady=(0, 20))

    class PrintLogger:
        def __init__(self, text_widget):
            self.text_widget = text_widget

        def write(self, message):
            self.text_widget.configure(state='normal')
            self.text_widget.insert(tk.END, message)
            self.text_widget.see(tk.END)
            self.text_widget.configure(state='disabled')
            self.text_widget.update_idletasks() # Wymusza odświeżenie tekstu zanim Matplotlib zwolni wątek!

        def flush(self):
            pass

    # Przekierowanie print i błędów do okna logów
    sys.stdout = PrintLogger(log_widget)
    sys.stderr = PrintLogger(log_widget)

    print("Aplikacja gotowa. Wybierz plik z listy powyżej.")

    root.mainloop()

if __name__ == "__main__":
    uruchom_gui()
