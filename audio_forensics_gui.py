#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════════╗
║        AUDIO FORENSICS ANALYZER  —  GUI Edition  v2.0                  ║
║        Strumento forense per l'analisi di file audio                    ║
║        Supporta: WAV · MP3 · OGG · FLAC · AIFF · M4A · OPUS · WMA     ║
╚══════════════════════════════════════════════════════════════════════════╝

Dipendenze (installa con pip):
    pip install librosa soundfile mutagen matplotlib numpy scipy

Per OGG/MP3 (se non disponibili):
    Windows: scarica FFmpeg da https://ffmpeg.org e aggiungilo al PATH
    Linux:   sudo apt install ffmpeg
"""

# ── stdlib ───────────────────────────────────────────────────────────────────
import os, sys, hashlib, json, struct, datetime, platform, threading
import subprocess, webbrowser, shutil
from pathlib import Path
from tkinter import (
    Tk, Frame, Label, Button, Entry, Text, Scrollbar, Listbox,
    Menu, StringVar, IntVar, BooleanVar, PhotoImage,
    filedialog, messagebox, ttk, font as tkfont
)
import tkinter as tk

# ── Verifica dipendenze scientifiche ─────────────────────────────────────────
MISSING = []
try:
    import numpy as np
except ImportError:
    MISSING.append("numpy")
try:
    import librosa
    import librosa.display
except ImportError:
    MISSING.append("librosa")
try:
    import soundfile as sf
except ImportError:
    MISSING.append("soundfile")
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.gridspec as gridspec
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
except ImportError:
    MISSING.append("matplotlib")
try:
    from mutagen import File as MutagenFile
except ImportError:
    MISSING.append("mutagen")
try:
    from scipy.stats import kurtosis, skew
except ImportError:
    MISSING.append("scipy")

# ─────────────────────────────────────────────────────────────────────────────
# COSTANTI ANALISI
# ─────────────────────────────────────────────────────────────────────────────
VERSION = "2.0.0"
SUPPORTED_EXT = {".wav",".mp3",".ogg",".flac",".aiff",".aif",
                 ".m4a",".wma",".opus",".oga",".mp4",".3gp"}
SILENCE_DB      = -60.0
CLIPPING_THR    = 0.999
SILENCE_MIN_SEC = 1.0
DC_THR          = 0.01

# ─────────────────────────────────────────────────────────────────────────────
# COLORI  (palette Windows 11 dark + accento forensics)
# ─────────────────────────────────────────────────────────────────────────────
C = {
    "bg":         "#1c1c1e",   # sfondo finestra principale
    "panel":      "#2c2c2e",   # pannelli
    "panel2":     "#3a3a3c",   # pannelli secondari / card
    "border":     "#48484a",   # bordi
    "accent":     "#0a84ff",   # blu Windows 11
    "accent2":    "#30d158",   # verde ok
    "warn":       "#ff9f0a",   # arancio warning
    "danger":     "#ff453a",   # rosso
    "text":       "#f2f2f7",   # testo primario
    "text2":      "#aeaeb2",   # testo secondario
    "text3":      "#636366",   # testo disabilitato
    "titlebar":   "#1c1c1e",
    "toolbar":    "#252527",
    "statusbar":  "#1c1c1e",
    "log_bg":     "#111113",
    "log_ok":     "#30d158",
    "log_warn":   "#ff9f0a",
    "log_err":    "#ff453a",
    "log_info":   "#64d2ff",
    "sel":        "#0a84ff",
    "listbg":     "#232325",
}

# ─────────────────────────────────────────────────────────────────────────────
# ENGINE FORENSE  (identico alla versione CLI, senza stampe dirette)
# ─────────────────────────────────────────────────────────────────────────────

def compute_hashes(filepath):
    h = {"md5": hashlib.md5(), "sha1": hashlib.sha1(), "sha256": hashlib.sha256()}
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            for v in h.values(): v.update(chunk)
    return {k: v.hexdigest().upper() for k, v in h.items()}

def format_bytes(sz):
    for u in ["B","KB","MB","GB"]:
        if sz < 1024: return f"{sz:.2f} {u}"
        sz /= 1024
    return f"{sz:.2f} TB"

def format_dur(s):
    h=int(s//3600); m=int((s%3600)//60); sec=s%60
    return f"{h:02d}:{m:02d}:{sec:06.3f}" if h>0 else f"{m:02d}:{sec:06.3f}"

def db_to_lin(db): return 10**(db/20)
def lin_to_db(v):
    if v<=0: return -float("inf")
    return 20*np.log10(abs(v))

def detect_magic(filepath):
    MAP = {
        b"RIFF": "WAV (RIFF)", b"OggS": "OGG", b"fLaC": "FLAC",
        b"\xff\xfb": "MP3 (CBR)", b"ID3": "MP3 (ID3)", b"FORM": "AIFF",
    }
    try:
        hdr = open(filepath,"rb").read(32)
        for magic, fmt in MAP.items():
            if hdr[:len(magic)]==magic: return fmt
        if hdr[0]==0xFF and (hdr[1]&0xE0)==0xE0: return "MP3"
        return f"Sconosciuto ({hdr[:4].hex()})"
    except: return "Errore lettura"

def extract_metadata(filepath):
    meta={}
    try:
        af = MutagenFile(filepath, easy=True)
        if af:
            for k,v in af.items():
                meta[k] = str(v[0]) if isinstance(v,list) and v else str(v)
            if hasattr(af,"info"):
                info=af.info
                for a in ["length","bitrate","sample_rate","channels","bits_per_sample","codec"]:
                    if hasattr(info,a): meta[f"[info] {a}"] = getattr(info,a)
    except Exception as e: meta["_error"]=str(e)
    return meta

def ogg_header_check(filepath):
    res={"valid":False,"pages":0,"serials":[]}
    try:
        data=open(filepath,"rb").read(65536)
        off=0; pages=0; serials=set()
        while off<len(data)-27:
            if data[off:off+4]==b"OggS" and data[off+4]==0:
                pages+=1
                serials.add(struct.unpack_from("<I",data,off+14)[0])
                sc=data[off+26]
                if off+27+sc<=len(data):
                    ps=sum(data[off+27:off+27+sc])
                    off+=27+sc+ps
                else: break
            else: off+=1
        res.update({"valid":pages>0,"pages":pages,"serials":list(serials)})
    except Exception as e: res["error"]=str(e)
    return res

def load_audio(filepath):
    try:
        y,sr=sf.read(filepath, always_2d=False)
        if y.ndim>1: y=y.mean(axis=1)
        return y.astype(np.float32),sr
    except:
        y,sr=librosa.load(filepath, sr=None, mono=True)
        return y,sr

def analyze_waveform(y,sr):
    dur=len(y)/sr
    rms=float(np.sqrt(np.mean(y**2)))
    peak=float(np.max(np.abs(y)))
    dc=float(np.mean(y))
    cf=peak/rms if rms>0 else 0
    blk=int(sr*0.05)
    if blk>0 and len(y)>=blk:
        blist=[y[i:i+blk] for i in range(0,len(y)-blk,blk)]
        brms=[np.sqrt(np.mean(b**2)) for b in blist if len(b)==blk]
        bdb=[lin_to_db(r) for r in brms if r>0]
        dr=max(bdb)-min(bdb) if bdb else 0.0
    else: dr=0.0
    return {
        "duration_sec":round(dur,4),"duration_fmt":format_dur(dur),
        "sample_rate":sr,"num_samples":len(y),
        "rms_db":round(lin_to_db(rms),2),"rms_linear":round(rms,6),
        "peak_db":round(lin_to_db(peak),2),"peak_linear":round(peak,6),
        "dc_offset":round(dc,6),"crest_factor_db":round(lin_to_db(cf),2),
        "kurtosis":round(float(kurtosis(y)),4),"skewness":round(float(skew(y)),4),
        "zcr":round(float(np.mean(librosa.feature.zero_crossing_rate(y))),6),
        "dynamic_range_db":round(dr,2),
    }

def detect_anomalies(y,sr,wf):
    anoms=[]
    # Clipping
    cc=int(np.sum(np.abs(y)>=CLIPPING_THR))
    if cc>0:
        pct=100*cc/len(y)
        sev="ALTA" if pct>1 else "MEDIA" if pct>0.1 else "BASSA"
        anoms.append({"tipo":"CLIPPING","severità":sev,
            "dettaglio":f"{cc} campioni ({pct:.3f}%)",
            "forense":"Saturazione o manipolazione"})
    # DC
    if abs(wf["dc_offset"])>DC_THR:
        anoms.append({"tipo":"DC OFFSET","severità":"MEDIA",
            "dettaglio":f"Offset={wf['dc_offset']:.4f}",
            "forense":"Problema hardware o editing"})
    # Silenzio
    fl=int(sr*0.1); hl=fl//2; thr=db_to_lin(SILENCE_DB)
    if len(y)>=fl:
        frames=librosa.util.frame(y,frame_length=fl,hop_length=hl)
        is_sil=np.sqrt(np.mean(frames**2,axis=0))<thr
        in_s=False; st=0
        for i,s in enumerate(is_sil):
            if s and not in_s: in_s=True; st=i
            elif not s and in_s:
                in_s=False
                d=((i-st)*hl)/sr
                if d>=SILENCE_MIN_SEC:
                    anoms.append({"tipo":"SILENZIO ANOMALO","severità":"MEDIA" if d>3 else "BASSA",
                        "dettaglio":f"{d:.2f}s @ t={format_dur(st*hl/sr)}",
                        "forense":"Taglio/cancellazione"})
        if in_s:
            d=((len(is_sil)-st)*hl)/sr
            if d>=SILENCE_MIN_SEC:
                anoms.append({"tipo":"SILENZIO ANOMALO","severità":"BASSA",
                    "dettaglio":f"{d:.2f}s @ t={format_dur(st*hl/sr)}",
                    "forense":"Taglio/cancellazione"})
    # Discontinuità
    bs=int(sr*0.5)
    if len(y)>=bs*2:
        brms=[np.sqrt(np.mean(y[i:i+bs]**2)) for i in range(0,len(y)-bs,bs)]
        ba=np.array(brms); diffs=np.abs(np.diff(ba)); m=np.mean(ba)
        if m>0:
            for idx in np.where(diffs/m>5.0)[0]:
                anoms.append({"tipo":"DISCONTINUITÀ","severità":"ALTA",
                    "dettaglio":f"Salto ampiezza @ t={format_dur(idx*0.5)}s (ratio={diffs[idx]/m:.1f}x)",
                    "forense":"Sospetto punto di splice/editing"})
    # Kurtosis
    if wf["kurtosis"]>10:
        anoms.append({"tipo":"KURTOSIS ELEVATA","severità":"BASSA",
            "dettaglio":f"k={wf['kurtosis']} (atteso 0-6)",
            "forense":"Impulsi/click o manipolazione"})
    return anoms

def spectral_features(y,sr):
    try:
        sc=librosa.feature.spectral_centroid(y=y,sr=sr)[0]
        sb=librosa.feature.spectral_bandwidth(y=y,sr=sr)[0]
        sr_=librosa.feature.spectral_rolloff(y=y,sr=sr,roll_percent=0.85)[0]
        mfcc=librosa.feature.mfcc(y=y,sr=sr,n_mfcc=13)
        return {
            "centroid_mean_hz":round(float(np.mean(sc)),2),
            "centroid_std_hz":round(float(np.std(sc)),2),
            "bandwidth_mean_hz":round(float(np.mean(sb)),2),
            "rolloff_mean_hz":round(float(np.mean(sr_)),2),
            "mfcc_means":[round(float(m),4) for m in np.mean(mfcc,axis=1)],
        }
    except Exception as e: return {"error":str(e)}

def integrity_verdict(anoms,fmt,ext):
    score=100; flags=[]
    sev_map={"ALTA":20,"MEDIA":10,"BASSA":5}
    for a in anoms:
        score-=sev_map.get(a["severità"],5)
        flags.append(f"{a['tipo']} [{a['severità']}]")
    ext=ext.lower()
    mismatch=(
        (ext==".ogg" and "ogg" not in fmt.lower()) or
        (ext==".mp3" and "mp3" not in fmt.lower()) or
        (ext==".wav" and "riff" not in fmt.lower()) or
        (ext==".flac" and "flac" not in fmt.lower())
    )
    if mismatch:
        score-=30; flags.append(f"MISMATCH FORMATO ({ext} vs {fmt})")
    score=max(0,score)
    if score>=85:   verdict,color="INTEGRO",    C["accent2"]
    elif score>=60: verdict,color="SOSPETTO",   C["warn"]
    else:           verdict,color="NON ATTENDIBILE", C["danger"]
    return {"score":score,"verdict":verdict,"color":color,
            "flags":flags,"mismatch":mismatch}

def generate_plots_fig(y,sr):
    """Genera figura matplotlib da embeddare nella GUI."""
    plt.style.use("dark_background")
    fig=plt.figure(figsize=(14,9), facecolor="#1c1c1e")
    gs=gridspec.GridSpec(3,2,figure=fig,hspace=0.5,wspace=0.38)
    TC="#e0e0f0"; AC="#0a84ff"; WC="#ff453a"
    dur=len(y)/sr
    tax=np.linspace(0,dur,len(y))

    # 1. Waveform
    ax1=fig.add_subplot(gs[0,:])
    ax1.set_facecolor("#111113")
    ax1.plot(tax,y,color=AC,lw=0.4,alpha=0.85)
    cm=np.abs(y)>=CLIPPING_THR
    if np.any(cm): ax1.scatter(tax[cm],y[cm],color=WC,s=3,zorder=5,label="Clipping")
    ax1.axhline(0,color="#444",lw=0.5,ls="--")
    ax1.axhline(CLIPPING_THR,color=WC,lw=0.6,ls=":",alpha=0.7)
    ax1.axhline(-CLIPPING_THR,color=WC,lw=0.6,ls=":",alpha=0.7)
    ax1.set_xlabel("Tempo (s)",color=TC,fontsize=8)
    ax1.set_ylabel("Ampiezza",color=TC,fontsize=8)
    ax1.set_title("FORMA D'ONDA",color=AC,fontsize=10,fontweight="bold",pad=6)
    ax1.tick_params(colors=TC,labelsize=7)
    for sp in ax1.spines.values(): sp.set_edgecolor("#333")

    # 2. Spettrogramma
    ax2=fig.add_subplot(gs[1,0])
    ax2.set_facecolor("#111113")
    D=librosa.amplitude_to_db(np.abs(librosa.stft(y)),ref=np.max)
    img=librosa.display.specshow(D,sr=sr,x_axis="time",y_axis="hz",ax=ax2,cmap="magma")
    fig.colorbar(img,ax=ax2,format="%+2.0f dB",pad=0.02)
    ax2.set_title("SPETTROGRAMMA",color=AC,fontsize=9,fontweight="bold",pad=5)
    ax2.tick_params(colors=TC,labelsize=7)
    ax2.set_xlabel("Tempo (s)",color=TC,fontsize=7)
    ax2.set_ylabel("Freq (Hz)",color=TC,fontsize=7)

    # 3. RMS nel tempo
    ax3=fig.add_subplot(gs[1,1])
    ax3.set_facecolor("#111113")
    hop=512
    rms_f=librosa.feature.rms(y=y,hop_length=hop)[0]
    rdb=librosa.amplitude_to_db(rms_f,ref=np.max)
    trms=librosa.frames_to_time(np.arange(len(rms_f)),sr=sr,hop_length=hop)
    ax3.fill_between(trms,rdb,-80,alpha=0.5,color="#7b2d8b")
    ax3.plot(trms,rdb,color="#d080ff",lw=0.8)
    ax3.axhline(SILENCE_DB,color=WC,lw=0.8,ls="--",alpha=0.8)
    ax3.set_ylim(-80,5)
    ax3.set_title("RMS NEL TEMPO",color=AC,fontsize=9,fontweight="bold",pad=5)
    ax3.tick_params(colors=TC,labelsize=7)
    ax3.set_xlabel("Tempo (s)",color=TC,fontsize=7)
    ax3.set_ylabel("RMS (dB)",color=TC,fontsize=7)

    # 4. FFT media
    ax4=fig.add_subplot(gs[2,0])
    ax4.set_facecolor("#111113")
    fs=min(4096,len(y)); nw=max(1,len(y)//fs)
    sp_acc=[]
    for i in range(nw):
        ch=y[i*fs:(i+1)*fs]
        if len(ch)==fs:
            w=np.hanning(fs)
            sp_acc.append(np.abs(np.fft.rfft(ch*w)))
    if sp_acc:
        avg=np.mean(sp_acc,axis=0)
        adb=20*np.log10(avg/(np.max(avg)+1e-12)+1e-12)
        freqs=np.fft.rfftfreq(fs,1/sr)
        ax4.semilogx(freqs[1:],adb[1:],color="#30d158",lw=0.8)
        ax4.fill_between(freqs[1:],adb[1:],-120,alpha=0.3,color="#007a30")
    ax4.set_xlim(20,sr//2); ax4.set_ylim(-80,5)
    ax4.set_title("SPETTRO FFT",color=AC,fontsize=9,fontweight="bold",pad=5)
    ax4.tick_params(colors=TC,labelsize=7)
    ax4.set_xlabel("Frequenza (Hz)",color=TC,fontsize=7)
    ax4.set_ylabel("dB",color=TC,fontsize=7)
    ax4.grid(axis="x",color="#222",lw=0.4,alpha=0.8)

    # 5. Distribuzione ampiezza
    ax5=fig.add_subplot(gs[2,1])
    ax5.set_facecolor("#111113")
    hv,be=np.histogram(y,bins=200,range=(-1,1))
    bc=(be[:-1]+be[1:])/2
    ax5.bar(bc,hv,width=be[1]-be[0],color="#ff9f0a",alpha=0.75,edgecolor="none")
    ax5.axvline(CLIPPING_THR,color=WC,lw=1.2,ls="--")
    ax5.axvline(-CLIPPING_THR,color=WC,lw=1.2,ls="--")
    ax5.set_title("DISTRIBUZIONE AMPIEZZA",color=AC,fontsize=9,fontweight="bold",pad=5)
    ax5.tick_params(colors=TC,labelsize=7)
    ax5.set_xlabel("Ampiezza norm.",color=TC,fontsize=7)
    ax5.set_ylabel("Campioni",color=TC,fontsize=7)

    fig.tight_layout(rect=[0,0,1,0.97])
    return fig

def save_plots_png(fig, out_dir, basename):
    path=os.path.join(out_dir,f"{basename}_forensics.png")
    fig.savefig(path,dpi=150,bbox_inches="tight",facecolor=fig.get_facecolor())
    return path

def generate_html_report(data,plot_path,out_path):
    """Report HTML (riutilizzato dalla versione CLI)."""
    iv=data["integrity"]; vc=iv["color"]; sc=iv["score"]
    hashes=data["hashes"]; wf=data["waveform"]; fi=data["file_info"]
    anoms=data["anomalies"]; meta=data["metadata"]; sp=data["spectral"]
    plot_rel=os.path.basename(plot_path)
    ts=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    def row(l,v,mono=False):
        st='font-family:monospace;font-size:12px;color:#a8d8ff;' if mono else ''
        return f'<tr><td class="lbl">{l}</td><td style="{st}">{v}</td></tr>'
    def arow(a):
        sc_map={"ALTA":"#ff453a","MEDIA":"#ff9f0a","BASSA":"#64d2ff"}
        c=sc_map.get(a.get("severità","BASSA"),"#aaa")
        return(f'<div class="anom" style="border-left:4px solid {c}">'
               f'<span style="color:{c};font-weight:700">[{a["severità"]}] {a["tipo"]}</span><br>'
               f'<span style="font-size:12px">{a["dettaglio"]}</span><br>'
               f'<span style="font-size:11px;color:#888;font-style:italic">⚖ {a["forense"]}</span></div>')
    ano_html="\n".join(arow(a) for a in anoms) if anoms else '<p style="color:#30d158">✓ Nessuna anomalia rilevata</p>'
    meta_rows="".join(row(k,v) for k,v in meta.items() if not k.startswith("_")) or row("—","Nessun metadato")
    spec_rows="".join(row(k,v) for k,v in sp.items() if k not in("mfcc_means","error"))
    flags_html=""
    if iv["flags"]:
        flags_html='<div class="flags">'+"".join(f'<span class="flag">{f}</span>' for f in iv["flags"])+"</div>"
    html=f"""<!DOCTYPE html><html lang="it"><head><meta charset="UTF-8">
<title>Report Forense — {fi['filename']}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#1c1c1e;color:#f2f2f7;font-family:'Segoe UI',sans-serif;font-size:14px;line-height:1.6}}
.hdr{{background:#111113;border-bottom:2px solid {vc};padding:20px 28px}}
.hdr h1{{font-size:20px;color:{vc};letter-spacing:2px;text-transform:uppercase}}
.hdr .sub{{color:#636366;font-size:11px;font-family:monospace;margin-top:4px}}
.vb{{display:flex;align-items:center;gap:16px;background:#2c2c2e;border:2px solid {vc};
     border-radius:8px;padding:14px 20px;margin:20px 28px}}
.vlbl{{font-size:24px;font-weight:700;color:{vc};letter-spacing:2px}}
.vscore{{margin-left:auto;text-align:right}}
.snum{{font-family:monospace;font-size:44px;color:{vc};line-height:1}}
.slbl{{color:#636366;font-size:10px;text-transform:uppercase;letter-spacing:1px}}
.cont{{padding:0 28px 40px}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}}
.card{{background:#2c2c2e;border:1px solid #3a3a3c;border-radius:6px;overflow:hidden}}
.full{{grid-column:1/-1}}
.ctitle{{background:#232325;padding:8px 14px;font-size:10px;font-weight:700;
         color:{vc};letter-spacing:2px;text-transform:uppercase;border-bottom:1px solid #3a3a3c}}
.cbody{{padding:12px 14px}}
table{{width:100%;border-collapse:collapse}}
tr:nth-child(even){{background:rgba(255,255,255,.025)}}
td{{padding:4px 8px;font-size:12px;vertical-align:top}}
td.lbl{{color:#636366;width:44%;border-right:1px solid #3a3a3c;font-size:11px}}
.hb{{font-family:monospace;font-size:11px;color:#a8d8ff;word-break:break-all;
     padding:6px 10px;background:#111113;border-radius:4px;margin-bottom:8px}}
.hlbl{{color:#636366;font-size:10px;text-transform:uppercase;letter-spacing:1px;margin-bottom:2px}}
.anom{{background:rgba(255,255,255,.03);border-radius:4px;padding:8px 12px;margin-bottom:8px}}
.flags{{display:flex;flex-wrap:wrap;gap:8px;padding:12px 14px}}
.flag{{background:rgba(255,69,58,.1);border:1px solid #ff453a;color:#ff453a;
       border-radius:4px;padding:2px 8px;font-size:11px;font-family:monospace}}
img{{width:100%;border-radius:4px}}
.footer{{text-align:center;color:#636366;font-size:11px;padding:16px;
         border-top:1px solid #3a3a3c;font-family:monospace}}
@media(max-width:700px){{.grid{{grid-template-columns:1fr}}}}
</style></head><body>
<div class="hdr">
  <h1>⚖ Report Forense Audio</h1>
  <div class="sub">Generato: {ts} · Audio Forensics Analyzer v{VERSION} · {platform.system()} {platform.release()}</div>
</div>
<div class="vb">
  <div style="font-size:32px">{'✅' if iv['verdict']=='INTEGRO' else '⚠️' if iv['verdict']=='SOSPETTO' else '🚨'}</div>
  <div><div class="vlbl">{iv['verdict']}</div>
  <div style="color:#636366;font-size:11px;margin-top:2px">Valutazione attendibilità forense</div></div>
  <div class="vscore"><div class="snum">{sc}</div><div class="slbl">Score / 100</div></div>
</div>
<div class="cont">
  <div class="card full" style="margin-bottom:16px">
    <div class="ctitle">📊 Analisi Visuale</div>
    <div class="cbody" style="padding:0"><img src="{plot_rel}" alt="Grafici forensi"></div>
  </div>
  {flags_html and f'<div class="card full" style="margin-bottom:16px"><div class="ctitle">🚩 Flag Forensi</div>{flags_html}</div>'}
  <div class="grid">
    <div class="card"><div class="ctitle">📁 File</div><div class="cbody"><table>
      {row("Nome",fi['filename'])}{row("Dim.",fi['size_human'])}
      {row("Formato rilevato",fi['format_detected'])}{row("Estensione",fi['extension'])}
      {row("Mismatch","⚠ SÌ" if iv['mismatch'] else "✓ No")}{row("Modifica",fi['mtime'])}
    </table></div></div>
    <div class="card"><div class="ctitle">〰 Forma d'Onda</div><div class="cbody"><table>
      {row("Durata",wf['duration_fmt'])}{row("Sample rate",f"{wf['sample_rate']:,} Hz")}
      {row("Campioni",f"{wf['num_samples']:,}")}{row("RMS",f"{wf['rms_db']} dB")}
      {row("Peak",f"{wf['peak_db']} dB")}{row("DC Offset",str(wf['dc_offset']))}
      {row("Crest Factor",f"{wf['crest_factor_db']} dB")}{row("Dyn. Range",f"{wf['dynamic_range_db']} dB")}
      {row("Kurtosis",str(wf['kurtosis']))}{row("Skewness",str(wf['skewness']))}
    </table></div></div>
    <div class="card"><div class="ctitle">🔐 Hash</div><div class="cbody">
      <div class="hlbl">SHA-1</div><div class="hb">{hashes['sha1']}</div>
      <div class="hlbl">SHA-256</div><div class="hb">{hashes['sha256']}</div>
      <div class="hlbl">MD5</div><div class="hb">{hashes['md5']}</div>
    </div></div>
    <div class="card"><div class="ctitle">🔍 Anomalie ({len(anoms)})</div>
      <div class="cbody">{ano_html}</div></div>
    <div class="card"><div class="ctitle">📡 Spettrale</div><div class="cbody"><table>{spec_rows}</table></div></div>
    <div class="card"><div class="ctitle">🏷 Metadati</div><div class="cbody"><table>{meta_rows}</table></div></div>
  </div>
</div>
<div class="footer">Audio Forensics Analyzer v{VERSION} · Il report non sostituisce una perizia tecnica certificata.</div>
</body></html>"""
    with open(out_path,"w",encoding="utf-8") as f: f.write(html)

def run_analysis(filepath, out_dir, progress_cb, log_cb):
    """
    Esegue analisi completa. progress_cb(pct, msg), log_cb(msg, tag).
    Ritorna dict risultati o solleva eccezione.
    """
    filepath=os.path.abspath(filepath)
    bn=Path(filepath).stem; ext=Path(filepath).suffix
    os.makedirs(out_dir, exist_ok=True)

    log_cb(f"▶ Avvio analisi: {os.path.basename(filepath)}","info")
    progress_cb(5,"Verifica file...")

    st=os.stat(filepath)
    fi={
        "filename":os.path.basename(filepath),"filepath":filepath,
        "size_bytes":st.st_size,"size_human":format_bytes(st.st_size),
        "extension":ext,"format_detected":detect_magic(filepath),
        "mtime":datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
    }
    log_cb(f"  Formato rilevato: {fi['format_detected']}","info")
    log_cb(f"  Dimensione: {fi['size_human']}","info")
    progress_cb(15,"Calcolo hash...")

    hashes=compute_hashes(filepath)
    log_cb(f"  SHA1:   {hashes['sha1']}","ok")
    log_cb(f"  SHA256: {hashes['sha256']}","ok")
    log_cb(f"  MD5:    {hashes['md5']}","ok")
    progress_cb(28,"Metadati...")

    meta=extract_metadata(filepath)
    log_cb(f"  Metadati estratti: {len(meta)} campi","info")
    ogg_info={}
    if ext.lower() in (".ogg",".oga",".opus"):
        ogg_info=ogg_header_check(filepath)
        log_cb(f"  OGG header: {'✓ valido' if ogg_info['valid'] else '✗ non valido'} — {ogg_info['pages']} pagine","ok" if ogg_info["valid"] else "warn")
    progress_cb(40,"Caricamento audio...")

    y,sr=load_audio(filepath)
    log_cb(f"  Audio caricato: {len(y):,} campioni @ {sr:,} Hz","info")
    progress_cb(52,"Analisi forma d'onda...")

    wf=analyze_waveform(y,sr)
    log_cb(f"  Durata: {wf['duration_fmt']} | RMS: {wf['rms_db']} dB | Peak: {wf['peak_db']} dB","info")
    progress_cb(65,"Rilevamento anomalie...")

    anoms=detect_anomalies(y,sr,wf)
    if anoms:
        for a in anoms:
            sc_col={"ALTA":"err","MEDIA":"warn","BASSA":"info"}.get(a["severità"],"info")
            log_cb(f"  ⚑ [{a['severità']}] {a['tipo']}: {a['dettaglio']}",sc_col)
    else:
        log_cb("  ✓ Nessuna anomalia rilevata","ok")
    progress_cb(75,"Analisi spettrale...")

    sp=spectral_features(y,sr)
    log_cb(f"  Centroide spettrale: {sp.get('centroid_mean_hz','N/A')} Hz","info")
    progress_cb(83,"Generazione grafici...")

    fig=generate_plots_fig(y,sr)
    plot_path=save_plots_png(fig,out_dir,bn)
    log_cb(f"  Grafici salvati: {os.path.basename(plot_path)}","info")
    progress_cb(92,"Generazione report...")

    iv=integrity_verdict(anoms,fi["format_detected"],ext)
    data={
        "file_info":fi,"hashes":hashes,"metadata":meta,
        "ogg_info":ogg_info,"waveform":wf,"anomalies":anoms,
        "spectral":sp,"integrity":iv,
    }
    report_path=os.path.join(out_dir,f"{bn}_report.html")
    generate_html_report(data,plot_path,report_path)

    json_path=os.path.join(out_dir,f"{bn}_forensics.json")
    with open(json_path,"w",encoding="utf-8") as f:
        json.dump(data,f,ensure_ascii=False,indent=2,default=str)

    log_cb(f"  Report HTML: {os.path.basename(report_path)}","ok")
    log_cb(f"  JSON raw:    {os.path.basename(json_path)}","ok")
    progress_cb(100,"Analisi completata")
    vc_map={"INTEGRO":"ok","SOSPETTO":"warn","NON ATTENDIBILE":"err"}
    log_cb(f"\n  ══ VERDETTO: {iv['verdict']}  |  Score: {iv['score']}/100 ══",vc_map[iv["verdict"]])

    data["_report_path"]=report_path
    data["_plot_path"]=plot_path
    data["_fig"]=fig
    return data


# ═════════════════════════════════════════════════════════════════════════════
# GUI
# ═════════════════════════════════════════════════════════════════════════════

class ToolTip:
    """Tooltip semplice per widget tkinter."""
    def __init__(self, widget, text):
        self.widget=widget; self.text=text; self.tip=None
        widget.bind("<Enter>",self.show); widget.bind("<Leave>",self.hide)
    def show(self,e=None):
        x=self.widget.winfo_rootx()+20; y=self.widget.winfo_rooty()+20
        self.tip=tk.Toplevel(self.widget); self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        Label(self.tip,text=self.text,background="#2c2c2e",foreground="#f2f2f7",
              relief="solid",borderwidth=1,font=("Segoe UI",9),padx=6,pady=3).pack()
    def hide(self,e=None):
        if self.tip: self.tip.destroy(); self.tip=None

class AudioForensicsApp(Tk):
    def __init__(self):
        super().__init__()
        self.title("Audio Forensics Analyzer  v" + VERSION)
        self.geometry("1180x760")
        self.minsize(900,600)
        self.configure(bg=C["bg"])
        self._set_icon()

        # Stato
        self.file_queue = []          # lista path aggiunti
        self.results    = {}          # path → result dict
        self.current_result = None
        self.analyzing  = False
        self.output_dir = tk.StringVar(value=os.path.join(os.path.expanduser("~"),"ForensicsOutput"))

        self._build_styles()
        self._build_menu()
        self._build_toolbar()
        self._build_main()
        self._build_statusbar()
        self._check_deps()

    # ── ICONA ────────────────────────────────────────────────────────────────
    def _set_icon(self):
        try:
            # Crea una piccola icona via PhotoImage (16×16 pixel pattern)
            icon_data = """
R0lGODlhEAAQAIABAAAAAP///yH5BAEKAAEALAAAAAAQABAAAAIjjI+pCu0Po4Qx
2oqzABjjzmzeA4bUlZqnOqruu8byTNf2BQA7"""
            pass  # icona opzionale, skip se non disponibile
        except: pass

    # ── STILI ttk ────────────────────────────────────────────────────────────
    def _build_styles(self):
        s=ttk.Style(self)
        s.theme_use("clam")
        # Notebook
        s.configure("TNotebook",background=C["panel"],borderwidth=0,tabmargins=[0,0,0,0])
        s.configure("TNotebook.Tab",background=C["panel2"],foreground=C["text2"],
                    padding=[14,6],font=("Segoe UI",9),borderwidth=0)
        s.map("TNotebook.Tab",
              background=[("selected",C["bg"]),("active",C["panel"])],
              foreground=[("selected",C["accent"]),("active",C["text"])])
        # Progressbar
        s.configure("Accent.Horizontal.TProgressbar",
                    troughcolor=C["panel2"],background=C["accent"],
                    borderwidth=0,thickness=6)
        # Treeview
        s.configure("Treeview",background=C["listbg"],foreground=C["text"],
                    fieldbackground=C["listbg"],rowheight=24,borderwidth=0,
                    font=("Segoe UI",9))
        s.configure("Treeview.Heading",background=C["panel2"],foreground=C["text2"],
                    font=("Segoe UI",9,"bold"),borderwidth=0)
        s.map("Treeview",background=[("selected",C["accent"])],
              foreground=[("selected","#ffffff")])
        # Separator
        s.configure("TSeparator",background=C["border"])
        # Scrollbar
        s.configure("TScrollbar",background=C["panel2"],troughcolor=C["panel"],
                    borderwidth=0,arrowsize=12)

    # ── MENU ─────────────────────────────────────────────────────────────────
    def _build_menu(self):
        mb=Menu(self,bg=C["panel"],fg=C["text"],activebackground=C["accent"],
                activeforeground="#fff",relief="flat",bd=0,font=("Segoe UI",9))
        self.config(menu=mb)

        fm=Menu(mb,tearoff=0,bg=C["panel"],fg=C["text"],
                activebackground=C["accent"],activeforeground="#fff",font=("Segoe UI",9))
        fm.add_command(label="Aggiungi file…\tCtrl+O",command=self.add_files)
        fm.add_command(label="Aggiungi cartella…",command=self.add_folder)
        fm.add_separator()
        fm.add_command(label="Imposta output…",command=self.choose_output)
        fm.add_separator()
        fm.add_command(label="Esci\tAlt+F4",command=self.destroy)
        mb.add_cascade(label="File",menu=fm)

        am=Menu(mb,tearoff=0,bg=C["panel"],fg=C["text"],
                activebackground=C["accent"],activeforeground="#fff",font=("Segoe UI",9))
        am.add_command(label="Analizza selezionati\tF5",command=self.run_selected)
        am.add_command(label="Analizza tutti\tF6",command=self.run_all)
        am.add_separator()
        am.add_command(label="Rimuovi selezionati",command=self.remove_selected)
        am.add_command(label="Svuota lista",command=self.clear_queue)
        mb.add_cascade(label="Analisi",menu=am)

        rm=Menu(mb,tearoff=0,bg=C["panel"],fg=C["text"],
                activebackground=C["accent"],activeforeground="#fff",font=("Segoe UI",9))
        rm.add_command(label="Apri report HTML",command=self.open_report)
        rm.add_command(label="Apri cartella output",command=self.open_output_dir)
        rm.add_command(label="Esporta JSON",command=self.export_json)
        mb.add_cascade(label="Report",menu=rm)

        hm=Menu(mb,tearoff=0,bg=C["panel"],fg=C["text"],
                activebackground=C["accent"],activeforeground="#fff",font=("Segoe UI",9))
        hm.add_command(label="Verifica dipendenze",command=self._check_deps_dialog)
        hm.add_separator()
        hm.add_command(label="Informazioni…",command=self.show_about)
        mb.add_cascade(label="?",menu=hm)

        # Bind tastiera
        self.bind("<Control-o>",lambda e: self.add_files())
        self.bind("<F5>",lambda e: self.run_selected())
        self.bind("<F6>",lambda e: self.run_all())

    # ── TOOLBAR ──────────────────────────────────────────────────────────────
    def _build_toolbar(self):
        tb=Frame(self,bg=C["toolbar"],height=42,bd=0)
        tb.pack(fill="x",side="top")
        tb.pack_propagate(False)

        def tbtn(parent,text,cmd,tip=""):
            b=Button(parent,text=text,command=cmd,
                     bg=C["toolbar"],fg=C["text"],activebackground=C["panel2"],
                     activeforeground=C["accent"],relief="flat",bd=0,padx=10,pady=6,
                     font=("Segoe UI",9),cursor="hand2")
            b.pack(side="left",padx=1,pady=4)
            if tip: ToolTip(b,tip)
            return b

        tbtn(tb,"📂 Aggiungi",self.add_files,"Aggiungi file audio (Ctrl+O)")
        tbtn(tb,"📁 Cartella",self.add_folder,"Aggiungi tutti i file di una cartella")
        Frame(tb,bg=C["border"],width=1).pack(side="left",fill="y",padx=4,pady=6)
        self.btn_run=tbtn(tb,"▶ Analizza",self.run_all,"Analizza tutti i file (F6)")
        self.btn_stop=tbtn(tb,"■ Stop",self.stop_analysis,"Interrompi analisi")
        self.btn_stop.config(state="disabled")
        Frame(tb,bg=C["border"],width=1).pack(side="left",fill="y",padx=4,pady=6)
        tbtn(tb,"🌐 Report",self.open_report,"Apri report HTML nel browser")
        tbtn(tb,"📂 Output",self.open_output_dir,"Apri cartella output")
        Frame(tb,bg=C["border"],width=1).pack(side="left",fill="y",padx=4,pady=6)

        # Output path inline
        Label(tb,text="Output:",bg=C["toolbar"],fg=C["text2"],
              font=("Segoe UI",9)).pack(side="left",padx=(4,2))
        self.out_entry=Entry(tb,textvariable=self.output_dir,
                             bg=C["panel2"],fg=C["text"],insertbackground=C["text"],
                             relief="flat",font=("Segoe UI",9),width=28,bd=0)
        self.out_entry.pack(side="left",ipady=4,padx=2)
        tbtn(tb,"…",self.choose_output,"Scegli cartella output")

    # ── LAYOUT PRINCIPALE ────────────────────────────────────────────────────
    def _build_main(self):
        # PanedWindow orizzontale: sinistra=lista file, destra=dettagli+log
        pw=tk.PanedWindow(self,orient="horizontal",bg=C["border"],
                          sashwidth=4,sashrelief="flat",bd=0)
        pw.pack(fill="both",expand=True,padx=0,pady=0)

        # ── PANNELLO SINISTRO: file queue ─────────────────────────────────
        left=Frame(pw,bg=C["panel"],width=280)
        pw.add(left,minsize=200)

        lh=Frame(left,bg=C["panel2"],height=32)
        lh.pack(fill="x"); lh.pack_propagate(False)
        Label(lh,text="FILE IN CODA",bg=C["panel2"],fg=C["accent"],
              font=("Segoe UI",8,"bold"),padx=10).pack(side="left",pady=6)
        self.lbl_count=Label(lh,text="0 file",bg=C["panel2"],fg=C["text3"],
                             font=("Segoe UI",8))
        self.lbl_count.pack(side="right",padx=8)

        # Treeview lista file
        cols=("stato","nome","dim")
        self.file_tree=ttk.Treeview(left,columns=cols,show="headings",
                                     selectmode="extended",style="Treeview")
        self.file_tree.heading("stato",text="")
        self.file_tree.heading("nome",text="Nome file")
        self.file_tree.heading("dim",text="Dim.")
        self.file_tree.column("stato",width=28,stretch=False,anchor="center")
        self.file_tree.column("nome",width=170,stretch=True)
        self.file_tree.column("dim",width=58,stretch=False,anchor="e")
        self.file_tree.pack(fill="both",expand=True,side="left")
        scr=ttk.Scrollbar(left,orient="vertical",command=self.file_tree.yview)
        scr.pack(side="right",fill="y")
        self.file_tree.configure(yscrollcommand=scr.set)

        # Tag colori per stato
        self.file_tree.tag_configure("ok",    foreground=C["accent2"])
        self.file_tree.tag_configure("warn",  foreground=C["warn"])
        self.file_tree.tag_configure("err",   foreground=C["danger"])
        self.file_tree.tag_configure("queue", foreground=C["text2"])
        self.file_tree.tag_configure("run",   foreground=C["accent"])

        # Drag & Drop simulato con click destro
        self.file_tree.bind("<Double-1>",lambda e: self.show_file_result())
        self.file_tree.bind("<Button-3>",self._context_menu)

        # Pulsanti sotto la lista
        btnf=Frame(left,bg=C["panel"],pady=4)
        btnf.pack(fill="x")
        Button(btnf,text="+ Aggiungi",command=self.add_files,
               bg=C["accent"],fg="#fff",relief="flat",font=("Segoe UI",8),
               cursor="hand2",padx=8,pady=3).pack(side="left",padx=6)
        Button(btnf,text="✕ Rimuovi",command=self.remove_selected,
               bg=C["panel2"],fg=C["text2"],relief="flat",font=("Segoe UI",8),
               cursor="hand2",padx=8,pady=3).pack(side="left",padx=2)
        Button(btnf,text="Svuota",command=self.clear_queue,
               bg=C["panel2"],fg=C["text2"],relief="flat",font=("Segoe UI",8),
               cursor="hand2",padx=8,pady=3).pack(side="right",padx=6)

        # ── PANNELLO DESTRO ───────────────────────────────────────────────
        right=Frame(pw,bg=C["bg"])
        pw.add(right,minsize=600)

        # PanedWindow verticale: risultati (alto) + log (basso)
        vpw=tk.PanedWindow(right,orient="vertical",bg=C["border"],
                           sashwidth=4,sashrelief="flat",bd=0)
        vpw.pack(fill="both",expand=True)

        # ── TAB RISULTATI ─────────────────────────────────────────────────
        nb_frame=Frame(vpw,bg=C["bg"])
        vpw.add(nb_frame,minsize=340)

        self.nb=ttk.Notebook(nb_frame,style="TNotebook")
        self.nb.pack(fill="both",expand=True,padx=0,pady=0)

        # Tab 1: Sommario
        self.tab_summary = self._make_summary_tab()
        # Tab 2: Grafici
        self.tab_charts  = self._make_charts_tab()
        # Tab 3: Dettagli
        self.tab_details = self._make_details_tab()
        # Tab 4: Hash & Metadati
        self.tab_hash    = self._make_hash_tab()

        # ── LOG CONSOLE ───────────────────────────────────────────────────
        log_frame=Frame(vpw,bg=C["log_bg"])
        vpw.add(log_frame,minsize=120)

        lh2=Frame(log_frame,bg=C["panel2"],height=26)
        lh2.pack(fill="x"); lh2.pack_propagate(False)
        Label(lh2,text="LOG ANALISI",bg=C["panel2"],fg=C["accent"],
              font=("Segoe UI",8,"bold"),padx=8).pack(side="left",pady=4)
        Button(lh2,text="Pulisci",command=self.clear_log,
               bg=C["panel2"],fg=C["text3"],relief="flat",
               font=("Segoe UI",7),cursor="hand2",padx=6,pady=0).pack(side="right",padx=6,pady=3)

        self.log_text=Text(log_frame,bg=C["log_bg"],fg=C["text"],
                           insertbackground=C["text"],relief="flat",
                           font=("Consolas",9),wrap="word",state="disabled",bd=0)
        self.log_text.pack(fill="both",expand=True,side="left")
        lscr=ttk.Scrollbar(log_frame,orient="vertical",command=self.log_text.yview)
        lscr.pack(side="right",fill="y")
        self.log_text.configure(yscrollcommand=lscr.set)
        # Tag colori log
        self.log_text.tag_configure("ok",  foreground=C["log_ok"])
        self.log_text.tag_configure("warn",foreground=C["log_warn"])
        self.log_text.tag_configure("err", foreground=C["log_err"])
        self.log_text.tag_configure("info",foreground=C["log_info"])
        self.log_text.tag_configure("norm",foreground=C["text"])

    # ── TAB: SOMMARIO ─────────────────────────────────────────────────────────
    def _make_summary_tab(self):
        f=Frame(self.nb,bg=C["bg"])
        self.nb.add(f,text="  Sommario  ")

        # Verdetto banner
        self.verd_frame=Frame(f,bg=C["panel2"],relief="flat",bd=0)
        self.verd_frame.pack(fill="x",padx=12,pady=(12,6))

        self.lbl_verd_icon=Label(self.verd_frame,text="—",font=("Segoe UI",28),
                                  bg=C["panel2"],fg=C["text3"])
        self.lbl_verd_icon.pack(side="left",padx=(12,6),pady=8)
        vt=Frame(self.verd_frame,bg=C["panel2"])
        vt.pack(side="left",pady=8)
        self.lbl_verd_text=Label(vt,text="Nessun file analizzato",
                                  font=("Segoe UI",16,"bold"),bg=C["panel2"],fg=C["text3"])
        self.lbl_verd_text.pack(anchor="w")
        self.lbl_verd_sub=Label(vt,text="Aggiungi file e avvia l'analisi",
                                 font=("Segoe UI",9),bg=C["panel2"],fg=C["text3"])
        self.lbl_verd_sub.pack(anchor="w")
        self.lbl_score=Label(self.verd_frame,text="—",font=("Consolas",36,"bold"),
                              bg=C["panel2"],fg=C["text3"])
        self.lbl_score.pack(side="right",padx=16,pady=8)

        # Griglia info rapide
        grid=Frame(f,bg=C["bg"])
        grid.pack(fill="x",padx=12,pady=4)
        grid.columnconfigure((0,1,2,3),weight=1,uniform="c")

        self.summary_cards={}
        for i,(key,label) in enumerate([
            ("duration","Durata"),("samplerate","Sample Rate"),
            ("peak","Peak"),("rms","RMS")
        ]):
            card=Frame(grid,bg=C["panel"],relief="flat",bd=0)
            card.grid(row=0,column=i,padx=4,pady=4,sticky="nsew")
            Label(card,text=label.upper(),font=("Segoe UI",8),bg=C["panel"],
                  fg=C["text3"]).pack(pady=(8,2))
            lv=Label(card,text="—",font=("Consolas",14,"bold"),
                     bg=C["panel"],fg=C["accent"])
            lv.pack(pady=(0,8))
            self.summary_cards[key]=lv

        # Anomalie
        af=Frame(f,bg=C["panel"],relief="flat")
        af.pack(fill="both",expand=True,padx=12,pady=(4,8))
        Label(af,text="ANOMALIE RILEVATE",font=("Segoe UI",8,"bold"),
              bg=C["panel"],fg=C["text3"],padx=10).pack(anchor="w",pady=(8,4))
        self.anom_tree=ttk.Treeview(af,
            columns=("sev","tipo","dettaglio","forense"),
            show="headings",height=6,style="Treeview")
        self.anom_tree.heading("sev",text="Severità")
        self.anom_tree.heading("tipo",text="Tipo")
        self.anom_tree.heading("dettaglio",text="Dettaglio")
        self.anom_tree.heading("forense",text="Implicazione forense")
        self.anom_tree.column("sev",width=80,stretch=False,anchor="center")
        self.anom_tree.column("tipo",width=140,stretch=False)
        self.anom_tree.column("dettaglio",width=200,stretch=True)
        self.anom_tree.column("forense",width=220,stretch=True)
        self.anom_tree.pack(fill="both",expand=True,side="left")
        ascr=ttk.Scrollbar(af,orient="vertical",command=self.anom_tree.yview)
        ascr.pack(side="right",fill="y")
        self.anom_tree.configure(yscrollcommand=ascr.set)
        self.anom_tree.tag_configure("ALTA",foreground=C["danger"])
        self.anom_tree.tag_configure("MEDIA",foreground=C["warn"])
        self.anom_tree.tag_configure("BASSA",foreground=C["log_info"])

        # Barra progresso analisi
        pf=Frame(f,bg=C["bg"])
        pf.pack(fill="x",padx=12,pady=(0,6))
        self.progress_bar=ttk.Progressbar(pf,style="Accent.Horizontal.TProgressbar",
                                           orient="horizontal",mode="determinate")
        self.progress_bar.pack(fill="x",side="left",expand=True)
        self.lbl_progress=Label(pf,text="",font=("Segoe UI",8),
                                 bg=C["bg"],fg=C["text2"],width=26,anchor="e")
        self.lbl_progress.pack(side="right",padx=(6,0))
        return f

    # ── TAB: GRAFICI ─────────────────────────────────────────────────────────
    def _make_charts_tab(self):
        f=Frame(self.nb,bg=C["bg"])
        self.nb.add(f,text="  Grafici  ")
        self.chart_placeholder=Label(f,text="Nessun grafico disponibile.\nEsegui prima un'analisi.",
                                      bg=C["bg"],fg=C["text3"],font=("Segoe UI",12))
        self.chart_placeholder.pack(expand=True)
        self.chart_canvas_widget=None
        return f

    # ── TAB: DETTAGLI ─────────────────────────────────────────────────────────
    def _make_details_tab(self):
        f=Frame(self.nb,bg=C["bg"])
        self.nb.add(f,text="  Dettagli  ")
        cols=("parametro","valore")
        self.detail_tree=ttk.Treeview(f,columns=cols,show="headings",style="Treeview")
        self.detail_tree.heading("parametro",text="Parametro")
        self.detail_tree.heading("valore",text="Valore")
        self.detail_tree.column("parametro",width=240,stretch=False)
        self.detail_tree.column("valore",width=400,stretch=True)
        self.detail_tree.pack(fill="both",expand=True,side="left")
        dscr=ttk.Scrollbar(f,orient="vertical",command=self.detail_tree.yview)
        dscr.pack(side="right",fill="y")
        self.detail_tree.configure(yscrollcommand=dscr.set)
        return f

    # ── TAB: HASH ─────────────────────────────────────────────────────────────
    def _make_hash_tab(self):
        f=Frame(self.nb,bg=C["bg"])
        self.nb.add(f,text="  Hash & Metadati  ")

        # Hash section
        hf=Frame(f,bg=C["panel"],relief="flat")
        hf.pack(fill="x",padx=12,pady=(12,6))
        Label(hf,text="HASH DI INTEGRITÀ",font=("Segoe UI",8,"bold"),
              bg=C["panel"],fg=C["text3"],padx=10).pack(anchor="w",pady=(8,4))

        self.hash_vars={}
        for algo in ("SHA-1","SHA-256","MD5"):
            row_f=Frame(hf,bg=C["panel"])
            row_f.pack(fill="x",padx=10,pady=2)
            Label(row_f,text=algo+":",font=("Segoe UI",9,"bold"),bg=C["panel"],
                  fg=C["text2"],width=8,anchor="e").pack(side="left")
            v=tk.StringVar(value="—")
            self.hash_vars[algo]=v
            e=Entry(row_f,textvariable=v,state="readonly",relief="flat",
                    bg=C["panel2"],fg=C["log_info"],readonlybackground=C["panel2"],
                    font=("Consolas",9),bd=0)
            e.pack(side="left",fill="x",expand=True,ipady=4,padx=(4,0))
            Button(row_f,text="Copia",command=lambda a=algo: self._copy_hash(a),
                   bg=C["panel2"],fg=C["text2"],relief="flat",font=("Segoe UI",8),
                   cursor="hand2",padx=6,pady=2).pack(side="left",padx=(4,8))
        Label(hf,text="",bg=C["panel"]).pack(pady=2)

        # Hash confronto
        cmp_f=Frame(f,bg=C["panel2"],relief="flat")
        cmp_f.pack(fill="x",padx=12,pady=(0,6))
        Label(cmp_f,text="CONFRONTA HASH (verifica catena di custodia)",
              font=("Segoe UI",8,"bold"),bg=C["panel2"],fg=C["text3"],padx=10).pack(anchor="w",pady=(8,4))
        cmp_row=Frame(cmp_f,bg=C["panel2"])
        cmp_row.pack(fill="x",padx=10,pady=(0,8))
        self.cmp_entry=Entry(cmp_row,bg=C["panel"],fg=C["text"],insertbackground=C["text"],
                             relief="flat",font=("Consolas",9),bd=0)
        self.cmp_entry.pack(side="left",fill="x",expand=True,ipady=5,padx=(0,4))
        Button(cmp_row,text="Verifica",command=self.compare_hash,
               bg=C["accent"],fg="#fff",relief="flat",font=("Segoe UI",9),
               cursor="hand2",padx=12,pady=4).pack(side="left")
        self.lbl_cmp=Label(cmp_f,text="",bg=C["panel2"],font=("Segoe UI",9))
        self.lbl_cmp.pack(anchor="w",padx=10,pady=(0,6))

        # Metadati
        mf=Frame(f,bg=C["panel"],relief="flat")
        mf.pack(fill="both",expand=True,padx=12,pady=(0,8))
        Label(mf,text="METADATI INCORPORATI",font=("Segoe UI",8,"bold"),
              bg=C["panel"],fg=C["text3"],padx=10).pack(anchor="w",pady=(8,4))
        cols2=("chiave","valore")
        self.meta_tree=ttk.Treeview(mf,columns=cols2,show="headings",
                                     height=8,style="Treeview")
        self.meta_tree.heading("chiave",text="Chiave")
        self.meta_tree.heading("valore",text="Valore")
        self.meta_tree.column("chiave",width=200,stretch=False)
        self.meta_tree.column("valore",width=400,stretch=True)
        self.meta_tree.pack(fill="both",expand=True,side="left")
        mscr=ttk.Scrollbar(mf,orient="vertical",command=self.meta_tree.yview)
        mscr.pack(side="right",fill="y")
        self.meta_tree.configure(yscrollcommand=mscr.set)
        return f

    # ── STATUSBAR ─────────────────────────────────────────────────────────────
    def _build_statusbar(self):
        sb=Frame(self,bg=C["statusbar"],height=22,bd=0)
        sb.pack(fill="x",side="bottom")
        sb.pack_propagate(False)
        self.lbl_status=Label(sb,text=f"Audio Forensics Analyzer v{VERSION}  —  Pronto",
                               bg=C["statusbar"],fg=C["text3"],font=("Segoe UI",8),padx=8)
        self.lbl_status.pack(side="left")
        self.lbl_status_r=Label(sb,text=platform.system()+" "+platform.release(),
                                 bg=C["statusbar"],fg=C["text3"],font=("Segoe UI",8),padx=8)
        self.lbl_status_r.pack(side="right")

    # ── MENU CONTESTUALE ──────────────────────────────────────────────────────
    def _context_menu(self,event):
        sel=self.file_tree.selection()
        if not sel: return
        m=Menu(self,tearoff=0,bg=C["panel"],fg=C["text"],
               activebackground=C["accent"],activeforeground="#fff",font=("Segoe UI",9))
        m.add_command(label="Analizza selezionati",command=self.run_selected)
        m.add_command(label="Apri report HTML",command=self.open_report)
        m.add_separator()
        m.add_command(label="Mostra in Esplora file",command=self.reveal_in_explorer)
        m.add_separator()
        m.add_command(label="Rimuovi",command=self.remove_selected)
        try: m.tk_popup(event.x_root,event.y_root)
        finally: m.grab_release()

    # ── AZIONI FILE ───────────────────────────────────────────────────────────
    def add_files(self):
        paths=filedialog.askopenfilenames(
            title="Seleziona file audio",
            filetypes=[
                ("File audio","*.wav *.mp3 *.ogg *.flac *.aiff *.aif *.m4a *.wma *.opus *.oga *.mp4 *.3gp"),
                ("Tutti i file","*.*")
            ]
        )
        for p in paths:
            self._queue_file(p)
        self._update_count()

    def add_folder(self):
        folder=filedialog.askdirectory(title="Seleziona cartella")
        if not folder: return
        added=0
        for root,dirs,files in os.walk(folder):
            for fn in files:
                if Path(fn).suffix.lower() in SUPPORTED_EXT:
                    self._queue_file(os.path.join(root,fn))
                    added+=1
        self._update_count()
        self.log(f"Cartella aggiunta: {added} file trovati in {folder}","info")

    def _queue_file(self,path):
        if path in self.file_queue: return
        self.file_queue.append(path)
        size=format_bytes(os.path.getsize(path))
        self.file_tree.insert("","end",iid=path,
                               values=("⏳",os.path.basename(path),size),
                               tags=("queue",))

    def remove_selected(self):
        for iid in self.file_tree.selection():
            self.file_queue.remove(iid)
            self.file_tree.delete(iid)
        self._update_count()

    def clear_queue(self):
        if self.analyzing:
            messagebox.showwarning("Analisi in corso","Attendere il completamento o premere Stop.")
            return
        self.file_queue.clear()
        for i in self.file_tree.get_children():
            self.file_tree.delete(i)
        self.results.clear()
        self._update_count()

    def choose_output(self):
        d=filedialog.askdirectory(title="Cartella output",initialdir=self.output_dir.get())
        if d: self.output_dir.set(d)

    def _update_count(self):
        n=len(self.file_queue)
        self.lbl_count.config(text=f"{n} file{'s' if n!=1 else ''}")

    def reveal_in_explorer(self):
        sel=self.file_tree.selection()
        if not sel: return
        path=sel[0]
        if platform.system()=="Windows":
            subprocess.Popen(["explorer","/select,",path])
        elif platform.system()=="Darwin":
            subprocess.Popen(["open","-R",path])
        else:
            subprocess.Popen(["xdg-open",os.path.dirname(path)])

    # ── ANALISI ───────────────────────────────────────────────────────────────
    def run_all(self):
        self._start_analysis(self.file_queue[:])

    def run_selected(self):
        sel=list(self.file_tree.selection())
        if not sel:
            messagebox.showinfo("Nessuna selezione","Seleziona almeno un file dalla lista.")
            return
        self._start_analysis(sel)

    def stop_analysis(self):
        self._stop_flag=True
        self.log("⚠ Interruzione richiesta...","warn")

    def _start_analysis(self,files):
        if not files:
            messagebox.showinfo("Lista vuota","Aggiungi file audio prima di avviare l'analisi.")
            return
        if MISSING:
            messagebox.showerror("Dipendenze mancanti",
                                  f"Installa prima:\n  pip install {' '.join(MISSING)}")
            return
        if self.analyzing: return
        self.analyzing=True; self._stop_flag=False
        self.btn_run.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.set_status("Analisi in corso…")

        def worker():
            out=self.output_dir.get()
            os.makedirs(out,exist_ok=True)
            for path in files:
                if self._stop_flag: break
                self.after(0,lambda p=path: self.file_tree.item(
                    p,values=("🔄",os.path.basename(p),
                              format_bytes(os.path.getsize(p))),tags=("run",)))
                self.after(0,self.nb.select,0)
                try:
                    data=run_analysis(
                        path, out,
                        lambda pct,msg,p=path: self.after(0,self._on_progress,pct,msg,p),
                        lambda msg,tag="norm": self.after(0,self.log,msg,tag)
                    )
                    self.results[path]=data
                    verd=data["integrity"]["verdict"]
                    tag={"INTEGRO":"ok","SOSPETTO":"warn","NON ATTENDIBILE":"err"}.get(verd,"queue")
                    icon={"INTEGRO":"✅","SOSPETTO":"⚠️","NON ATTENDIBILE":"🚨"}.get(verd,"?")
                    self.after(0,lambda p=path,t=tag,ic=icon:
                               self.file_tree.item(p,values=(ic,os.path.basename(p),
                                                              format_bytes(os.path.getsize(p))),tags=(t,)))
                    self.after(0,self._display_result,data)
                except Exception as e:
                    self.after(0,self.log,f"ERRORE {os.path.basename(path)}: {e}","err")
                    self.after(0,lambda p=path:
                               self.file_tree.item(p,values=("✗",os.path.basename(p),"—"),tags=("err",)))
            self.after(0,self._analysis_done)
        threading.Thread(target=worker,daemon=True).start()

    def _on_progress(self,pct,msg,filepath):
        self.progress_bar["value"]=pct
        self.lbl_progress.config(text=msg[:30])
        self.set_status(f"{os.path.basename(filepath)} — {msg}")

    def _analysis_done(self):
        self.analyzing=False
        self.btn_run.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.progress_bar["value"]=100
        self.lbl_progress.config(text="Completato ✓")
        self.set_status("Analisi completata.")
        self.log("","norm")

    # ── DISPLAY RISULTATI ─────────────────────────────────────────────────────
    def _display_result(self,data):
        self.current_result=data
        iv=data["integrity"]; wf=data["waveform"]; fi=data["file_info"]

        # ── Sommario
        vmap={"INTEGRO":("✅",C["accent2"]),"SOSPETTO":("⚠️",C["warn"]),
              "NON ATTENDIBILE":("🚨",C["danger"])}
        icon,col=vmap.get(iv["verdict"],("—",C["text3"]))
        self.lbl_verd_icon.config(text=icon,fg=col,bg=C["panel2"])
        self.lbl_verd_text.config(text=iv["verdict"],fg=col)
        self.lbl_verd_sub.config(
            text=f"{fi['filename']}  ·  Formato: {fi['format_detected']}",
            fg=C["text2"])
        self.lbl_score.config(text=str(iv["score"]),fg=col)
        self.summary_cards["duration"].config(text=wf["duration_fmt"])
        self.summary_cards["samplerate"].config(text=f"{wf['sample_rate']:,} Hz")
        self.summary_cards["peak"].config(text=f"{wf['peak_db']} dB")
        self.summary_cards["rms"].config(text=f"{wf['rms_db']} dB")

        # Anomalie treeview
        for i in self.anom_tree.get_children():
            self.anom_tree.delete(i)
        if data["anomalies"]:
            for a in data["anomalies"]:
                self.anom_tree.insert("","end",
                    values=(a["severità"],a["tipo"],a["dettaglio"],a["forense"]),
                    tags=(a["severità"],))
        else:
            self.anom_tree.insert("","end",values=("—","Nessuna anomalia","✓ File integro",""))

        # ── Grafici
        if self.chart_canvas_widget:
            self.chart_canvas_widget.get_tk_widget().destroy()
            self.chart_canvas_widget=None
        if self.chart_placeholder:
            self.chart_placeholder.destroy()
            self.chart_placeholder=None
        fig=data.get("_fig")
        if fig:
            try:
                canvas=FigureCanvasTkAgg(fig,master=self.tab_charts)
                canvas.draw()
                canvas.get_tk_widget().pack(fill="both",expand=True)
                self.chart_canvas_widget=canvas
            except Exception as e:
                Label(self.tab_charts,text=f"Errore grafico: {e}",
                      bg=C["bg"],fg=C["danger"]).pack(expand=True)

        # ── Dettagli
        for i in self.detail_tree.get_children():
            self.detail_tree.delete(i)
        # File info
        self.detail_tree.insert("","end",values=("── FILE INFO ──",""),tags=("head",))
        for k,v in fi.items():
            self.detail_tree.insert("","end",values=(k,v))
        # Waveform
        self.detail_tree.insert("","end",values=("── FORMA D'ONDA ──",""),tags=("head",))
        for k,v in wf.items():
            self.detail_tree.insert("","end",values=(k,v))
        # Spettrale
        sp=data.get("spectral",{})
        self.detail_tree.insert("","end",values=("── SPETTRALE ──",""),tags=("head",))
        for k,v in sp.items():
            if k!="mfcc_means": self.detail_tree.insert("","end",values=(k,v))
        # OGG
        if data.get("ogg_info"):
            self.detail_tree.insert("","end",values=("── OGG HEADER ──",""),tags=("head",))
            for k,v in data["ogg_info"].items():
                self.detail_tree.insert("","end",values=(k,v))
        self.detail_tree.tag_configure("head",foreground=C["accent"],font=("Segoe UI",9,"bold"))

        # ── Hash & Metadati
        h=data["hashes"]
        self.hash_vars["SHA-1"].set(h.get("sha1","—"))
        self.hash_vars["SHA-256"].set(h.get("sha256","—"))
        self.hash_vars["MD5"].set(h.get("md5","—"))
        self.lbl_cmp.config(text="",bg=C["panel2"])

        for i in self.meta_tree.get_children():
            self.meta_tree.delete(i)
        meta=data.get("metadata",{})
        if meta:
            for k,v in meta.items():
                self.meta_tree.insert("","end",values=(k,str(v)))
        else:
            self.meta_tree.insert("","end",values=("—","Nessun metadato disponibile"))

    def show_file_result(self):
        sel=self.file_tree.selection()
        if not sel: return
        path=sel[0]
        if path in self.results:
            self._display_result(self.results[path])

    # ── HASH CONFRONTO ────────────────────────────────────────────────────────
    def compare_hash(self):
        ref=self.cmp_entry.get().strip().upper()
        if not ref or not self.current_result: return
        h=self.current_result["hashes"]
        for algo,val in [("sha1",h.get("sha1","")),
                          ("sha256",h.get("sha256","")),
                          ("md5",h.get("md5",""))]:
            if val.upper()==ref:
                self.lbl_cmp.config(
                    text=f"✓ CORRISPONDENZA TROVATA  ({algo.upper()})",
                    fg=C["accent2"],bg=C["panel2"])
                return
        self.lbl_cmp.config(
            text="✗ NESSUNA CORRISPONDENZA  —  File potenzialmente alterato!",
            fg=C["danger"],bg=C["panel2"])

    def _copy_hash(self,algo):
        v=self.hash_vars.get(algo)
        if v:
            self.clipboard_clear(); self.clipboard_append(v.get())
            self.set_status(f"{algo} copiato negli appunti")

    # ── REPORT & OUTPUT ───────────────────────────────────────────────────────
    def open_report(self):
        if not self.current_result:
            messagebox.showinfo("Nessun risultato","Esegui prima un'analisi.")
            return
        rp=self.current_result.get("_report_path")
        if rp and os.path.isfile(rp):
            webbrowser.open(f"file://{os.path.abspath(rp)}")
        else:
            messagebox.showerror("Report non trovato","Il file report non è stato trovato.")

    def open_output_dir(self):
        d=self.output_dir.get()
        os.makedirs(d,exist_ok=True)
        if platform.system()=="Windows":
            os.startfile(d)
        elif platform.system()=="Darwin":
            subprocess.Popen(["open",d])
        else:
            subprocess.Popen(["xdg-open",d])

    def export_json(self):
        if not self.current_result:
            messagebox.showinfo("Nessun risultato","Esegui prima un'analisi.")
            return
        dest=filedialog.asksaveasfilename(
            defaultextension=".json",filetypes=[("JSON","*.json"),("Tutti","*.*")],
            initialfile=f"{Path(self.current_result['file_info']['filename']).stem}_forensics.json")
        if dest:
            d={k:v for k,v in self.current_result.items() if not k.startswith("_")}
            with open(dest,"w",encoding="utf-8") as f:
                json.dump(d,f,ensure_ascii=False,indent=2,default=str)
            self.set_status(f"JSON esportato: {os.path.basename(dest)}")

    # ── LOG ───────────────────────────────────────────────────────────────────
    def log(self,msg,tag="norm"):
        ts=datetime.datetime.now().strftime("%H:%M:%S")
        self.log_text.config(state="normal")
        if msg:
            self.log_text.insert("end",f"[{ts}] {msg}\n",tag)
        else:
            self.log_text.insert("end","\n",tag)
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def clear_log(self):
        self.log_text.config(state="normal")
        self.log_text.delete("1.0","end")
        self.log_text.config(state="disabled")

    def set_status(self,msg):
        self.lbl_status.config(text=f"  {msg}")

    # ── DIPENDENZE ────────────────────────────────────────────────────────────
    def _check_deps(self):
        if MISSING:
            self.log(f"⚠ Dipendenze mancanti: {', '.join(MISSING)}","warn")
            self.log(f"  Installa con: pip install {' '.join(MISSING)}","warn")
        else:
            self.log("✓ Tutte le dipendenze soddisfatte","ok")
            self.log(f"  Python {sys.version.split()[0]} | librosa | soundfile | mutagen | scipy","info")

    def _check_deps_dialog(self):
        deps=["numpy","librosa","soundfile","matplotlib","mutagen","scipy"]
        lines=[]
        for d in deps:
            try:
                mod=__import__(d)
                v=getattr(mod,"__version__","?")
                lines.append(f"  ✓  {d:<14} {v}")
            except ImportError:
                lines.append(f"  ✗  {d:<14} NON INSTALLATO")
        messagebox.showinfo("Verifica dipendenze","\n".join(lines))

    # ── ABOUT ─────────────────────────────────────────────────────────────────
    def show_about(self):
        win=tk.Toplevel(self); win.title("Informazioni")
        win.geometry("420x260"); win.resizable(False,False)
        win.configure(bg=C["panel"])
        win.transient(self); win.grab_set()
        Label(win,text="⚖ Audio Forensics Analyzer",
              font=("Segoe UI",14,"bold"),bg=C["panel"],fg=C["accent"]).pack(pady=(20,4))
        Label(win,text=f"Versione {VERSION}",font=("Segoe UI",10),
              bg=C["panel"],fg=C["text2"]).pack()
        ttk.Separator(win,orient="horizontal").pack(fill="x",padx=20,pady=14)
        info=[
            ("Funzionalità","Hash MD5/SHA1/SHA256 · Analisi waveform"),
            ("","Rilevamento anomalie · Spettrogramma · Report HTML"),
            ("Formati","WAV · MP3 · OGG · FLAC · AIFF · M4A · WMA · OPUS"),
            ("Framework","Python + librosa + soundfile + mutagen + matplotlib"),
        ]
        for lbl,val in info:
            r=Frame(win,bg=C["panel"]); r.pack(fill="x",padx=20,pady=1)
            if lbl:
                Label(r,text=lbl+":",font=("Segoe UI",9,"bold"),bg=C["panel"],
                      fg=C["text3"],width=12,anchor="e").pack(side="left")
            Label(r,text=val,font=("Segoe UI",9),bg=C["panel"],fg=C["text"]).pack(side="left",padx=6)
        Button(win,text="Chiudi",command=win.destroy,bg=C["accent"],fg="#fff",
               relief="flat",font=("Segoe UI",9),padx=16,pady=4,cursor="hand2").pack(pady=18)


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def main():
    # Controlla se è richiesta modalità CLI (nessun argomento GUI)
    if len(sys.argv) > 1 and sys.argv[1] not in ("--gui",):
        # Modalità CLI minimale (compatibilità)
        import argparse
        parser=argparse.ArgumentParser(description="Audio Forensics Analyzer CLI")
        parser.add_argument("files",nargs="+")
        parser.add_argument("-o","--output",default="./forensics_output")
        args=parser.parse_args()
        os.makedirs(args.output,exist_ok=True)
        for fp in args.files:
            if os.path.isfile(fp):
                run_analysis(fp,args.output,
                             lambda p,m: print(f"[{p:3d}%] {m}"),
                             lambda m,t="": print(f"  {m}"))
        return

    app=AudioForensicsApp()
    app.mainloop()

if __name__=="__main__":
    main()
