import { useState } from "react";
import { fmtSize, type Model } from "../api";

function Copy({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button type="button" className="link" onClick={() => {
      navigator.clipboard?.writeText(text).then(() => { setDone(true); setTimeout(() => setDone(false), 1200); });
    }}>{done ? "已复制" : "复制"}</button>
  );
}

/** How to fetch a model by hand: URLs, target folder, file names, checksums. */
export default function DownloadInfo({ model, onRecheck }: { model: Model; onRecheck: () => void }) {
  const d = model.download;
  if (!d.dir) return <p className="muted small" style={{ margin: 0 }}>随程序安装，不需要下载。</p>;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10, padding: 14, background: "var(--surface-2)", borderRadius: 8 }}>
      <div className="small">
        <span className="muted">放到目录：</span>
        <span className="mono" style={{ overflowWrap: "anywhere" }}>{d.dir}</span> <Copy text={d.dir} />
      </div>
      {d.archive && (
        <div className="small">
          <span className="muted">先下载压缩包：</span>
          <a href={d.archive.url} target="_blank" rel="noreferrer" className="mono" style={{ overflowWrap: "anywhere" }}>{d.archive.name}</a>
          <span className="muted"> · {fmtSize(d.archive.size)}</span> <Copy text={d.archive.url} />
          <div className="muted">解压后，在里面按文件名找到下面的文件，放进上面的目录。</div>
        </div>
      )}
      <div className="scroll-x">
        <table className="grid small">
          <thead><tr><th>文件名</th><th>大小</th><th>{d.archive ? "" : "下载地址"}</th><th>SHA-256</th><th>状态</th></tr></thead>
          <tbody>
            {d.files.map((f) => (
              <tr key={f.name}>
                <td className="mono" style={{ overflowWrap: "anywhere" }}>{f.name} <Copy text={f.name} /></td>
                <td className="mono">{fmtSize(f.size)}</td>
                <td>
                  {f.urls.map((u, k) => (
                    <div key={u}>
                      <a href={u} target="_blank" rel="noreferrer" download>{k === 0 ? "下载" : `备用地址 ${k}`}</a> <Copy text={u} />
                    </div>
                  ))}
                </td>
                <td className="mono" title={f.sha256}>{f.sha256.slice(0, 12)}… <Copy text={f.sha256} /></td>
                <td>{f.present ? <span className="badge ok">已在</span> : <span className="badge warn">缺少</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row small" style={{ gap: 10, alignItems: "center" }}>
        <span className="muted">文件名要完全一致；放好后点</span>
        <button type="button" className="btn small" onClick={onRecheck}>重新校验</button>
        <span className="muted">（会核对大小和 SHA-256）。命令行：</span>
        <span className="mono">bapu models info {model.id}</span>
      </div>
    </div>
  );
}
