//! Host-owned filesystem grants. MCP roots constrain explicitly configured grants;
//! they never create rights or imply a read/write permission mode.
use crate::{Result, WorkspaceError};
use serde::Deserialize;
use std::{
    collections::BTreeSet,
    fs,
    io::Read,
    path::{Component, Path, PathBuf},
    sync::{Arc, RwLock},
};

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Mode {
    ReadOnly,
    ReadWrite,
}
#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Kind {
    Directory,
    File,
}
#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Lifetime {
    Once,
    Session,
    Project,
}
#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Grant {
    pub path: PathBuf,
    pub kind: Kind,
    pub mode: Mode,
    pub lifetime: Lifetime,
    pub session_id: Option<String>,
    pub approval_id: Option<String>,
    #[serde(default)]
    pub require_mcp_root: bool,
}
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Policy {
    version: u32,
    project: PathBuf,
    #[serde(default = "default_mode")]
    primary_mode: Mode,
    grants: Vec<Grant>,
}
fn default_mode() -> Mode {
    Mode::ReadWrite
}
#[derive(Debug)]
struct State {
    grants: Vec<Grant>,
    roots: Vec<PathBuf>,
    valid: bool,
    primary_mode: Mode,
    active_once: BTreeSet<String>,
}
#[derive(Clone, Debug)]
pub struct Access {
    primary: PathBuf,
    policy: Option<PathBuf>,
    session_id: Option<String>,
    state: Arc<RwLock<State>>,
}
fn denied(path: &Path, why: &str) -> WorkspaceError {
    WorkspaceError::AuthorizationRequired(format!(
        "authorization required for {}: {why}; ask the host to update --access policy (tool approval does not grant filesystem access)",
        path.display()
    ))
}
fn clean(path: &Path) -> Result<()> {
    if !path.is_absolute()
        || path
            .components()
            .any(|p| matches!(p, Component::ParentDir | Component::CurDir))
    {
        return Err(denied(path, "expected absolute path without traversal"));
    }
    if path.components().any(|p| p.as_os_str() == ".ffsake") {
        return Err(WorkspaceError::InternalPath(path.display().to_string()));
    }
    Ok(())
}
pub(crate) fn no_symlinks(path: &Path) -> Result<()> {
    let mut cursor = PathBuf::new();
    for part in path.components() {
        cursor.push(part);
        match fs::symlink_metadata(&cursor) {
            Ok(m) if m.file_type().is_symlink() => {
                return Err(denied(path, "symlink component is forbidden"));
            }
            Ok(_) => {}
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
            Err(e) => return Err(e.into()),
        }
    }
    Ok(())
}
impl Access {
    pub fn primary(primary: PathBuf) -> Self {
        Self {
            primary,
            policy: None,
            session_id: std::env::var("FFSAKE_HOST_SESSION_ID")
                .ok()
                .filter(|id| !id.is_empty()),
            state: Arc::new(RwLock::new(State {
                grants: vec![],
                roots: vec![],
                valid: true,
                primary_mode: Mode::ReadWrite,
                active_once: BTreeSet::new(),
            })),
        }
    }
    pub fn configured(primary: PathBuf, policy: &Path) -> Result<Self> {
        // Canonical path is fixed by host startup. Atomic regular-file replacement
        // is supported, but later symlink substitution is never followed.
        no_symlinks(policy)?;
        let policy = fs::canonicalize(policy)?;
        let access = Self {
            policy: Some(policy),
            ..Self::primary(primary)
        };
        if access.policy.as_ref().unwrap().starts_with(&access.primary) {
            return Err(denied(
                access.policy.as_ref().unwrap(),
                "host authorization must be stored outside the agent workspace",
            ));
        }
        access.refresh()?;
        Ok(access)
    }
    pub fn refresh(&self) -> Result<()> {
        self.state.write().unwrap().active_once.clear();
        let Some(path) = &self.policy else {
            return Ok(());
        };
        let read = (|| -> Result<(Vec<Grant>, Mode)> {
            no_symlinks(path)?;
            if !fs::metadata(path)?.is_file() {
                return Err(denied(path, "policy must be a regular file"));
            }
            let mut bytes = vec![];
            fs::File::open(path)?.take(65537).read_to_end(&mut bytes)?;
            if bytes.len() > 65536 {
                return Err(denied(path, "policy exceeds 64 KiB"));
            }
            let policy: Policy = serde_json::from_slice(&bytes)
                .map_err(|e| denied(path, &format!("invalid host policy: {e}")))?;
            if policy.project != self.primary {
                return Err(denied(
                    path,
                    "host authorization belongs to a different primary project",
                ));
            }
            if policy.version != 1 || policy.grants.len() > 64 {
                return Err(denied(
                    path,
                    "policy requires version 1 and at most 64 grants",
                ));
            }
            for grant in &policy.grants {
                clean(&grant.path)?;
                if grant.lifetime != Lifetime::Project
                    && grant
                        .session_id
                        .as_ref()
                        .is_none_or(|id| id.is_empty() || id.len() > 128)
                {
                    return Err(denied(
                        &grant.path,
                        "session/once grants require a bounded host session_id",
                    ));
                }
                if grant.lifetime == Lifetime::Once
                    && grant
                        .approval_id
                        .as_ref()
                        .is_none_or(|id| id.len() < 16 || id.len() > 128)
                {
                    return Err(denied(
                        &grant.path,
                        "once grants require a unique 16..128-byte approval_id",
                    ));
                }
                if path.starts_with(&grant.path) {
                    return Err(denied(
                        &grant.path,
                        "grant must not contain the host authorization file",
                    ));
                }
                no_symlinks(&grant.path)?;
                if grant.path == self.primary
                    || grant.path.starts_with(&self.primary)
                    || self.primary.starts_with(&grant.path)
                {
                    return Err(denied(
                        &grant.path,
                        "additional grants must be disjoint from primary root",
                    ));
                }
                if let Ok(m) = fs::metadata(&grant.path) {
                    if (grant.kind == Kind::Directory && !m.is_dir())
                        || (grant.kind == Kind::File && !m.is_file())
                    {
                        return Err(denied(&grant.path, "grant kind does not match filesystem"));
                    }
                } else if grant.kind == Kind::Directory {
                    return Err(denied(&grant.path, "directory grant must exist"));
                }
            }
            for (i, a) in policy.grants.iter().enumerate() {
                for b in &policy.grants[i + 1..] {
                    if a.path.starts_with(&b.path) || b.path.starts_with(&a.path) {
                        return Err(denied(&a.path, "overlapping grants are unsupported"));
                    }
                }
            }
            Ok((policy.grants, policy.primary_mode))
        })();
        let mut state = self.state.write().unwrap();
        match read {
            Ok((grants, primary_mode)) => {
                state.primary_mode = primary_mode;
                state.grants = grants;
                state.valid = true;
                Ok(())
            }
            Err(e) => {
                state.grants.clear();
                state.valid = false;
                Err(e)
            }
        }
    }
    pub fn roots(&self, roots: Vec<PathBuf>) -> Result<()> {
        if roots.len() > 64 {
            self.state.write().unwrap().roots.clear();
            return Err(denied(&self.primary, "too many MCP roots"));
        }
        for root in &roots {
            clean(root)?;
            no_symlinks(root)?;
        }
        self.state.write().unwrap().roots = roots;
        Ok(())
    }
    pub fn clear_roots(&self) {
        self.state.write().unwrap().roots.clear();
    }
    pub fn has_policy(&self) -> bool {
        self.policy.is_some()
    }
    pub(crate) fn absolute(&self, path: &Path) -> Result<PathBuf> {
        if path.as_os_str().is_empty()
            || path.components().any(|p| matches!(p, Component::ParentDir))
        {
            return Err(WorkspaceError::InvalidPath(path.display().to_string()));
        }
        let absolute = if path.is_absolute() {
            path.to_path_buf()
        } else {
            self.primary.join(path)
        };
        clean(&absolute)?;
        Ok(absolute)
    }
    pub(crate) fn authorize(&self, path: &Path, write: bool) -> Result<(PathBuf, PathBuf)> {
        let absolute = self.absolute(path)?;
        if let Some(policy) = &self.policy
            && (absolute == *policy || (write && policy.starts_with(&absolute)))
        {
            return Err(denied(path, "host policy and its ancestors are protected"));
        }
        if absolute.starts_with(&self.primary) {
            if write && self.state.read().unwrap().primary_mode == Mode::ReadOnly {
                return Err(denied(path, "primary project is read-only"));
            }
            no_symlinks(&absolute)?;
            if write && absolute == self.primary {
                return Err(WorkspaceError::InvalidPath(
                    "root mutation is forbidden".into(),
                ));
            }
            return Ok((absolute, self.primary.clone()));
        }
        let mut state = self.state.write().unwrap();
        if !state.valid {
            return Err(denied(
                path,
                "host policy unavailable; external grants revoked",
            ));
        }
        for grant in state.grants.clone() {
            let matches = absolute == grant.path
                || (grant.kind == Kind::Directory && absolute.starts_with(&grant.path));
            if !matches {
                continue;
            }
            if grant.require_mcp_root
                && !state.roots.iter().any(|root| grant.path.starts_with(root))
            {
                return Err(denied(
                    path,
                    "configured grant requires a currently advertised MCP root",
                ));
            }
            if write && grant.mode == Mode::ReadOnly {
                return Err(denied(
                    path,
                    "read/write grant required; current grant is read-only",
                ));
            }
            if write && grant.kind == Kind::Directory && absolute == grant.path {
                return Err(denied(path, "grant root mutation is forbidden"));
            }
            if grant.lifetime != Lifetime::Project
                && (self.session_id.is_none() || grant.session_id != self.session_id)
            {
                return Err(denied(path, "grant belongs to a different host session"));
            }
            no_symlinks(&absolute)?;
            if grant.lifetime == Lifetime::Once {
                let key = blake3::hash(grant.approval_id.as_ref().unwrap().as_bytes())
                    .to_hex()
                    .to_string();
                if !state.active_once.contains(&key) {
                    let directory = self.primary.join(".ffsake/access-consumed");
                    no_symlinks(&directory)?;
                    fs::create_dir_all(&directory)?;
                    let claim = directory.join(&key);
                    no_symlinks(&claim)?;
                    fs::OpenOptions::new()
                        .write(true)
                        .create_new(true)
                        .open(claim)
                        .map_err(|_| {
                            denied(
                                path,
                                "one-time approval already consumed or cannot be claimed",
                            )
                        })?;
                    state.active_once.insert(key);
                }
            }
            return Ok((absolute, grant.path.clone()));
        }
        Err(denied(
            path,
            if write {
                "explicit read/write grant required"
            } else {
                "explicit read grant required"
            },
        ))
    }
    pub(crate) fn transaction<'a>(
        &self,
        paths: impl IntoIterator<Item = &'a str>,
        write: bool,
    ) -> Result<()> {
        let mut owner = None;
        for path in paths {
            let (_, root) = self.authorize(Path::new(path), write)?;
            if owner.as_ref().is_some_and(|p| p != &root) {
                return Err(denied(
                    Path::new(path),
                    "cross-root transactions/context are unsupported; split into independent operations",
                ));
            }
            owner = Some(root);
        }
        Ok(())
    }
}
