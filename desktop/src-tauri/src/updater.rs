//! Apply a downloaded update to the portable program folder.
//!
//! The Python service downloads `Bapu-app-<version>.zip` into `data\update\`
//! and writes `staged.json` (version, zip name, SHA-256, signature). When Bapu
//! closes (or at the next start, if it could not), this replaces `Bapu.exe`,
//! `app\` and `tools\` with the new ones. `runtime\`, `models\` and `data\`
//! are never touched; if the package list changed, the next start syncs the
//! runtime (setup::needs_sync).
//!
//! The zip must carry an Ed25519 signature by the release key over
//! "bapu-update:<version>:<sha256>"; the public key is built in. A download
//! that was tampered with on the way (mirror, proxy) is refused.

use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};

use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

/// Public half of the release signing key (the private half never leaves the release machine).
const PUBLIC_KEY: &str = "b0dbd1cf0e3d880cbc7df27f5b34ffdce8177e4b398f6343bc1bc76babdabef4";

/// Top-level entries an update may contain.
const ALLOWED: &[&str] = &["Bapu.exe", "app", "tools", "README.txt"];

fn hex(s: &str) -> Option<Vec<u8>> {
    (s.len() % 2 == 0).then(|| (0..s.len()).step_by(2).map(|i| u8::from_str_radix(&s[i..i + 2], 16).ok()).collect())?
}

pub fn verify(version: &str, sha256: &str, signature: &str) -> Result<(), String> {
    let key: [u8; 32] = hex(PUBLIC_KEY).and_then(|v| v.try_into().ok()).ok_or("内置公钥损坏")?;
    let key = VerifyingKey::from_bytes(&key).map_err(|e| e.to_string())?;
    let sig: [u8; 64] = hex(signature).and_then(|v| v.try_into().ok()).ok_or("签名格式不对")?;
    key.verify(format!("bapu-update:{version}:{sha256}").as_bytes(), &Signature::from_bytes(&sig))
        .map_err(|_| "签名不对：这个更新包不是 Bapu 官方发布的，或者下载途中被改过".to_string())
}

fn log(home: &Path, msg: &str) {
    use std::io::Write;
    let p = home.join("data").join("logs").join("update.log");
    let _ = fs::create_dir_all(p.parent().unwrap());
    if let Ok(mut f) = fs::OpenOptions::new().create(true).append(true).open(p) {
        let _ = writeln!(f, "{msg}");
    }
}

/// Leftovers of the previous update (the old exe could not be deleted while it ran).
fn cleanup(home: &Path) {
    let _ = fs::remove_file(home.join("Bapu.old.exe"));
    let _ = fs::remove_dir_all(home.join("app.old"));
}

pub fn apply_staged(home: &Path) {
    cleanup(home);
    let dir = home.join("data").join("update");
    let Ok(text) = fs::read_to_string(dir.join("staged.json")) else { return };
    let result = apply(home, &dir, &text);
    let _ = fs::remove_file(dir.join("staged.json"));
    if result.is_err() {
        // a refused or broken download is not kept around (it is downloaded again next time)
        if let Some(name) = serde_json::from_str::<Value>(&text).ok().and_then(|s| s["zip"].as_str().map(str::to_string)) {
            let _ = fs::remove_file(dir.join(name));
        }
    }
    let _ = fs::remove_dir_all(dir.join("new"));
    let (ok, msg) = match &result {
        Ok(v) => (true, format!("已更新到 {v}")),
        Err(e) => (false, format!("更新没装上：{e}")),
    };
    log(home, &msg);
    let last = json!({ "ok": ok, "message": msg,
        "at": std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0) });
    let _ = fs::write(dir.join("last.json"), serde_json::to_vec_pretty(&last).unwrap());
}

fn apply(home: &Path, dir: &Path, staged: &str) -> Result<String, String> {
    let s: Value = serde_json::from_str(staged).map_err(|e| format!("staged.json：{e}"))?;
    let version = s["version"].as_str().ok_or("没有版本号")?;
    let zip_path = dir.join(s["zip"].as_str().ok_or("没有更新包文件名")?);
    let data = fs::read(&zip_path).map_err(|e| format!("读不了更新包：{e}"))?;
    let sha = format!("{:x}", Sha256::digest(&data));
    if Some(sha.as_str()) != s["sha256"].as_str() {
        return Err("更新包的 SHA-256 不对".into());
    }
    verify(version, &sha, s["signature"].as_str().unwrap_or_default())?;

    // unpack next to the program, refusing anything outside the allowed entries
    let new = dir.join("new");
    let _ = fs::remove_dir_all(&new);
    let mut zip = zip::ZipArchive::new(std::io::Cursor::new(&data)).map_err(|e| e.to_string())?;
    for i in 0..zip.len() {
        let mut f = zip.by_index(i).map_err(|e| e.to_string())?;
        let rel: PathBuf = f.enclosed_name().ok_or("更新包里有不安全的路径")?;
        let top = rel.components().next().map(|c| c.as_os_str().to_string_lossy().to_string()).unwrap_or_default();
        if !ALLOWED.contains(&top.as_str()) {
            return Err(format!("更新包里有不该有的东西：{top}"));
        }
        let out = new.join(&rel);
        if f.is_dir() {
            fs::create_dir_all(&out).map_err(|e| e.to_string())?;
            continue;
        }
        fs::create_dir_all(out.parent().unwrap()).map_err(|e| e.to_string())?;
        let mut buf = Vec::with_capacity(f.size() as usize);
        f.read_to_end(&mut buf).map_err(|e| e.to_string())?;
        fs::write(&out, buf).map_err(|e| e.to_string())?;
    }
    if !new.join("app").join("src").join("bapu").is_dir() || !new.join("Bapu.exe").is_file() {
        return Err("更新包不完整".into());
    }

    // swap app\ (rolled back if anything fails), then tools\ and the exe
    let app = home.join("app");
    let old = home.join("app.old");
    let _ = fs::remove_dir_all(&old);
    fs::rename(&app, &old).map_err(|e| format!("换不了 app 文件夹（还有文件在用？）：{e}"))?;
    if let Err(e) = fs::rename(new.join("app"), &app) {
        let _ = fs::rename(&old, &app);
        return Err(format!("换不了 app 文件夹：{e}"));
    }
    for name in ["tools", "README.txt"] {
        let src = new.join(name);
        if src.exists() {
            let dest = home.join(name);
            if dest.is_dir() {
                let _ = fs::remove_dir_all(&dest);
            } else {
                let _ = fs::remove_file(&dest);
            }
            fs::rename(&src, &dest).map_err(|e| format!("换不了 {name}：{e}"))?;
        }
    }
    // the running exe cannot be overwritten, but it can be renamed
    let exe = home.join("Bapu.exe");
    let parked = home.join("Bapu.old.exe");
    let _ = fs::remove_file(&parked);
    if exe.exists() {
        fs::rename(&exe, &parked).map_err(|e| format!("换不了 Bapu.exe：{e}"))?;
    }
    if let Err(e) = fs::rename(new.join("Bapu.exe"), &exe) {
        let _ = fs::rename(&parked, &exe);
        return Err(format!("换不了 Bapu.exe：{e}"));
    }
    let _ = fs::remove_dir_all(&old);
    let _ = fs::remove_file(&zip_path);
    Ok(version.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    // signed with the release key by scripts/release.py's sign()
    const SIG: &str = "fb3d9d21df683f3605ac92b4dbe67433242f9a44a69296c91361320065e61b23402eea2fd7e2881ddc345b01e7c1c6b2c0443f41b2e0667a89f38e2e02bd8105";

    #[test]
    fn release_signature_verifies_and_tampering_fails() {
        let sha = "ab".repeat(32);
        assert!(verify("0.0.0-test", &sha, SIG).is_ok());
        assert!(verify("0.0.1-test", &sha, SIG).is_err()); // another version
        assert!(verify("0.0.0-test", &"cd".repeat(32), SIG).is_err()); // another zip
        assert!(verify("0.0.0-test", &sha, &"00".repeat(64)).is_err());
    }
}
