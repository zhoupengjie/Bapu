import { useEffect, useState } from "react";
import { api, apiUrl, type JobDetail } from "../api";
import { useJob } from "../App";
import { inDesktop, revealInFolder } from "../desktop";
import { t } from "../i18n";
import { Icon } from "../icons";

const FORMATS = [
  { id: "svp", name: "SynthV 工程", detail: ".svp，带伴奏和主唱参考轨" },
  { id: "mid", name: "MIDI", detail: "带歌词" },
] as const;
type FormatId = (typeof FORMATS)[number]["id"];

const AUDIO = "accompaniment,lead_dry";

/** SV grid: 0 = as transcribed; 8 / 16 / 32 = beats on SV's beat lines, notes snapped to 1/n notes. */
const GRIDS = [[0, "不对齐"], [8, "1/8"], [16, "1/16"], [32, "1/32"]] as const;
const GRID_KEY = "bapu.exportGrid";
function savedGrid(): number {
  try {
    const v = Number(localStorage.getItem(GRID_KEY));
    return GRIDS.some(([g]) => g === v) && localStorage.getItem(GRID_KEY) !== null ? v : 16;
  } catch {
    return 16;
  }
}

export default function ExportPage({ id }: { id: string }) {
  const enc = encodeURIComponent(id);
  const { refresh } = useJob();
  const [job, setJob] = useState<JobDetail | null>(null);
  const [formats, setFormats] = useState<Record<FormatId, boolean>>({ svp: true, mid: true });
  const [grid, setGrid] = useState(savedGrid);
  const [files, setFiles] = useState<{ name: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.get<JobDetail>(`/jobs/${enc}`).then(setJob).catch((e) => setError((e as Error).message));
  }, [enc]);

  const chosen = FORMATS.filter((f) => formats[f.id]).map((f) => f.id);

  async function exportNow() {
    setBusy(true);
    setError(null);
    try {
      const r = await api.post<{ files: { name: string }[] }>(`/jobs/${enc}/export`, { formats: chosen, audio: AUDIO, grid });
      setFiles(r.files);
      api.get<JobDetail>(`/jobs/${enc}`).then(setJob).catch(() => {});
      refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const shown = files.length ? files : job?.exports ?? [];
  const desktop = inDesktop();
  const reveal = (name: string) => {
    if (!job) return;
    const sep = job.folder.includes("\\") ? "\\" : "/";
    revealInFolder(`${job.folder.replace(/[\\/]+$/, "")}${sep}${name}`);
  };

  return (
    <section className="card narrow" aria-labelledby="h-exp">
      <div>
        <h1 id="h-exp">{t("导出")}</h1>
        <p className="lead">{t("放在原曲旁边")}</p>
      </div>

      <div className="formats">
        {FORMATS.map((f) => {
          const on = formats[f.id];
          return (
            <button key={f.id} type="button" className={`fmt ${on ? "on" : ""}`} aria-pressed={on}
              onClick={() => setFormats({ ...formats, [f.id]: !on })}>
              <span className="box" aria-hidden="true">{on ? "✓" : ""}</span>
              <span>
                <b style={{ fontWeight: 500 }}>{t(f.name)}</b><br />
                <span className="muted" style={{ fontSize: 14 }}>{t(f.detail)}</span>
              </span>
            </button>
          );
        })}
      </div>

      <div className="rows">
        <div className="row">
          <span className="k">{t("对齐网格")}</span>
          <span className="v" />
          <span className="seg" role="group" aria-label={t("对齐网格")}>
            {GRIDS.map(([g, label]) => (
              <button key={g} type="button" className={grid === g ? "on" : ""} aria-pressed={grid === g}
                onClick={() => {
                  setGrid(g);
                  try {
                    localStorage.setItem(GRID_KEY, String(g));
                  } catch {
                    /* storage unavailable */
                  }
                }}>
                {t(label)}
              </button>
            ))}
          </span>
        </div>
      </div>

      {error && <div className="notice bad">{t(error)}</div>}

      <div className="foot">
        <span />
        <button type="button" className="btn primary" disabled={busy || !job?.has_score || chosen.length === 0} onClick={exportNow}>
          {busy ? t("导出中…") : job?.export_stale ? t("重新导出") : t("导出")}
        </button>
      </div>

      {shown.length > 0 && (
        <div className="files">
          {shown.map((f) => (
            <div key={f.name}>
              <a href={apiUrl(`/jobs/${enc}/export/${encodeURIComponent(f.name)}`)} download style={{ minWidth: 0, overflowWrap: "anywhere" }}>{f.name}</a>
              {desktop && (
                <button type="button" className="link" onClick={() => reveal(f.name)}>
                  <Icon name="folder" size={16} /> {t("打开文件夹")}
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
