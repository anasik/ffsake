//! Best-effort, rate-limited release awareness. No installation or MCP notifications.
use serde_json::{Value, json};
use std::{
    fs,
    path::{Path, PathBuf},
    process::Command,
    sync::{Arc, Mutex},
    time::{SystemTime, UNIX_EPOCH},
};

const DAY: u64 = 86400;
const API: &str = "https://api.github.com/repos/anasik/ffsake/releases?per_page=100";
fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}
fn version(s: &str) -> Option<(u64, u64, u64, u64)> {
    let (base, alpha) = s.strip_prefix('v')?.split_once("-alpha.")?;
    let v = base
        .split('.')
        .map(str::parse::<u64>)
        .collect::<Result<Vec<_>, _>>()
        .ok()?;
    if v.len() != 3 || alpha.is_empty() || !alpha.bytes().all(|x| x.is_ascii_digit()) {
        return None;
    }
    Some((v[0], v[1], v[2], alpha.parse().ok()?))
}
fn read_json(path: &Path) -> Option<Value> {
    let m = fs::symlink_metadata(path).ok()?;
    if !m.is_file() || m.file_type().is_symlink() || m.len() > 1024 * 1024 {
        return None;
    }
    serde_json::from_slice(&fs::read(path).ok()?).ok()
}
fn compatible_release(value: &Value, platform: &str, major: u64) -> Option<String> {
    value
        .as_array()?
        .iter()
        .filter_map(|r| {
            if r["draft"].as_bool() != Some(false) {
                return None;
            }
            let tag = r["tag_name"].as_str()?;
            let key = version(tag)?;
            if key.0 != major {
                return None;
            }
            let filename = format!("ffsake-{tag}-{platform}.tar.gz");
            let assets = r["assets"].as_array()?;
            if !assets.iter().any(|a| a["name"].as_str() == Some(&filename))
                || !assets
                    .iter()
                    .any(|a| a["name"].as_str() == Some("SHA256SUMS"))
            {
                return None;
            }
            Some((key, tag.to_owned()))
        })
        .max_by_key(|(key, _)| *key)
        .map(|(_, tag)| tag)
}
fn refresh(prefix: &Path, platform: &str, major: u64) -> Option<String> {
    let cache = prefix.join("update-cache.json");
    if let Some(v) = read_json(&cache) {
        let checked = v["checked_at"].as_u64().unwrap_or(0);
        if now().saturating_sub(checked) < DAY {
            return v["latest"]
                .as_str()
                .filter(|s| version(s).is_some())
                .map(str::to_owned);
        }
    }
    let lock = prefix.join(".update-check.lock");
    if lock.exists()
        && fs::metadata(&lock)
            .and_then(|m| m.modified())
            .ok()
            .and_then(|t| t.elapsed().ok())
            .is_some_and(|d| d.as_secs() > 60)
    {
        let _ = fs::remove_file(&lock);
    }
    let guard = match fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&lock)
    {
        Ok(guard) => guard,
        Err(_) => {
            // Another server owns the shared check. Wait only in this background thread.
            for _ in 0..40 {
                std::thread::sleep(std::time::Duration::from_millis(100));
                if let Some(v) = read_json(&cache)
                    && now().saturating_sub(v["checked_at"].as_u64().unwrap_or(0)) < DAY
                {
                    return v["latest"]
                        .as_str()
                        .filter(|s| version(s).is_some())
                        .map(str::to_owned);
                }
            }
            return None;
        }
    };
    let result = (|| {
        // Another instance may have refreshed the cache before we acquired the lock.
        if let Some(v) = read_json(&cache)
            && now().saturating_sub(v["checked_at"].as_u64().unwrap_or(0)) < DAY
        {
            return v["latest"]
                .as_str()
                .filter(|s| version(s).is_some())
                .map(str::to_owned);
        }
        let output = Command::new("/usr/bin/curl")
            .args([
                "--silent",
                "--show-error",
                "--max-time",
                "3",
                "--connect-timeout",
                "2",
                "--max-filesize",
                "1048576",
                "--user-agent",
                "ffsake-update-check",
                API,
            ])
            .output()
            .ok();
        let latest = output
            .filter(|o| o.status.success() && o.stdout.len() <= 1024 * 1024)
            .and_then(|o| serde_json::from_slice::<Value>(&o.stdout).ok())
            .and_then(|v| compatible_release(&v, platform, major));
        let data = json!({"checked_at":now(),"latest":latest});
        let tmp = prefix.join(format!(".update-cache-{}", std::process::id()));
        if let Ok(mut file) = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&tmp)
        {
            use std::io::Write;
            if file.write_all(data.to_string().as_bytes()).is_ok() {
                let _ = fs::rename(&tmp, &cache);
            } else {
                let _ = fs::remove_file(&tmp);
            }
        }
        latest
    })();
    drop(guard);
    let _ = fs::remove_file(lock);
    result
}
pub struct UpdateNotice {
    installed: String,
    prefix: PathBuf,
    platform: &'static str,
    last_start: u64,
    latest: Arc<Mutex<Option<String>>>,
    sent: Option<String>,
}
impl UpdateNotice {
    pub fn new() -> Option<Self> {
        let exe = std::env::current_exe().ok()?.canonicalize().ok()?;
        let generation = exe.parent()?;
        let manifest = read_json(&generation.join("manifest.json"))?;
        if manifest["release_channel"] != "alpha" || manifest["pinned"] != false {
            return None;
        }
        let installed = manifest["release_version"].as_str()?.to_owned();
        version(&installed)?;
        let prefix = generation.parent()?.parent()?.to_owned();
        let platform = if cfg!(all(target_os = "macos", target_arch = "aarch64")) {
            "macos-arm64"
        } else if cfg!(all(target_os = "linux", target_arch = "x86_64")) {
            "linux-x86_64"
        } else {
            return None;
        };
        Some(Self {
            installed,
            prefix,
            platform,
            last_start: 0,
            latest: Arc::new(Mutex::new(None)),
            sent: None,
        })
    }
    pub fn tick(&mut self) {
        let time = now();
        if self.last_start != 0 && time.saturating_sub(self.last_start) < DAY {
            return;
        }
        self.last_start = time;
        let prefix = self.prefix.clone();
        let platform = self.platform;
        let state = self.latest.clone();
        let major = version(&self.installed).map(|v| v.0).unwrap_or(0);
        std::thread::spawn(move || {
            let v = refresh(&prefix, platform, major);
            if let Ok(mut s) = state.lock() {
                *s = v;
            }
        });
    }
    pub fn notice(&mut self) -> Option<Value> {
        let latest = self.latest.lock().ok()?.clone()?;
        if version(&latest)?.0 != version(&self.installed)?.0
            || version(&latest)? <= version(&self.installed)?
            || self.sent.as_deref() == Some(&latest)
        {
            return None;
        }
        self.sent = Some(latest.clone());
        Some(
            json!({"installed_version":self.installed,"latest_version":latest,"channel":"alpha","action":"A newer release is available. Tell the user; to update, run the explicit FFSAKE installer update command and reconnect MCP. No update has been installed."}),
        )
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn alpha_versions_and_compatible_assets() {
        assert!(version("v0.1.0-alpha.10") > version("v0.1.0-alpha.9"));
        assert!(version("v0.1.0-rc.1").is_none());
        let releases = json!([{"draft":false,"tag_name":"v0.1.0-alpha.2","assets":[{"name":"ffsake-v0.1.0-alpha.2-linux-x86_64.tar.gz"},{"name":"SHA256SUMS"}]}]);
        assert_eq!(
            compatible_release(&releases, "linux-x86_64", 0).as_deref(),
            Some("v0.1.0-alpha.2")
        );
        assert_eq!(compatible_release(&releases, "macos-arm64", 0), None);
    }
}
