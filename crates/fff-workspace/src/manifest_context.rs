//! Bounded, declarative Cargo manifest import. Never invokes Cargo or discovers files.
use crate::Workspace;
use crate::lsp::CrateSpec;
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Component, Path, PathBuf};
use toml::Table;

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

fn table<'a>(value: &'a Table, key: &str) -> Result<Option<&'a Table>> {
    value
        .get(key)
        .map(|v| {
            v.as_table()
                .ok_or_else(|| format!("{key} must be a table").into())
        })
        .transpose()
}

fn string<'a>(value: &'a Table, key: &str) -> Result<Option<&'a str>> {
    value
        .get(key)
        .map(|v| {
            v.as_str()
                .ok_or_else(|| format!("{key} must be a string").into())
        })
        .transpose()
}

fn flag(value: &Table, key: &str, default: bool) -> Result<bool> {
    value.get(key).map_or(Ok(default), |v| {
        v.as_bool()
            .ok_or_else(|| format!("{key} must be boolean").into())
    })
}

fn relative(base: &Path, value: &str) -> Result<String> {
    if value.is_empty() || value.len() > 4096 || Path::new(value).is_absolute() {
        return Err("manifest path must be bounded and relative".into());
    }
    let mut path = PathBuf::new();
    for component in base.join(value).components() {
        match component {
            Component::Normal(part) => path.push(part),
            Component::CurDir => {}
            Component::ParentDir if path.pop() => {}
            _ => return Err("manifest path escapes workspace".into()),
        }
    }
    let path = path.to_str().ok_or("manifest path is not UTF-8")?;
    if path.is_empty() || path.len() > 4096 {
        return Err("invalid manifest path".into());
    }
    Ok(path.to_string())
}

fn crate_name(name: &str) -> Result<String> {
    let normalized = name.replace('-', "_");
    if !crate::lsp_project::dependency_name(&normalized) {
        return Err("manifest crate names must be bounded ordinary ASCII Rust names".into());
    }
    Ok(normalized)
}

fn note(notes: &mut Vec<Value>, manifest: &str, name: &str, reason: &str) -> Result<()> {
    let entry = json!({"manifest":manifest,"name":name,"reason":reason});
    if notes.contains(&entry) {
        return Ok(());
    }
    if notes.len() >= 128 || name.len() > 4096 {
        return Err("manifest import exceeds omission budget".into());
    }
    notes.push(entry);
    Ok(())
}

fn edition(package: &Table, inherited: Option<&Table>) -> Result<String> {
    let value = match package.get("edition") {
        None => "2015",
        Some(toml::Value::String(value)) => value,
        Some(toml::Value::Table(value))
            if value.len() == 1
                && value.get("workspace").and_then(toml::Value::as_bool) == Some(true) =>
        {
            inherited
                .and_then(|w| w.get("package"))
                .and_then(toml::Value::as_table)
                .and_then(|p| p.get("edition"))
                .and_then(toml::Value::as_str)
                .ok_or("inherited edition requires the supplied workspace manifest")?
        }
        _ => return Err("invalid manifest edition".into()),
    };
    if !matches!(value, "2015" | "2018" | "2021" | "2024") {
        return Err("unsupported manifest edition".into());
    }
    Ok(value.to_string())
}

struct Target {
    manifest: String,
    name: String,
    root: String,
    edition: String,
    library: bool,
}

fn targets(
    manifest: &str,
    document: &Table,
    inherited: Option<&Table>,
    sources: &BTreeSet<String>,
    notes: &mut Vec<Value>,
) -> Result<Vec<Target>> {
    let Some(package) = table(document, "package")? else {
        return Ok(vec![]);
    };
    let name = string(package, "name")?.ok_or("package name is required")?;
    crate_name(name)?;
    let package_edition = edition(package, inherited)?;
    let base = Path::new(manifest)
        .parent()
        .ok_or("invalid manifest parent")?;
    let library = table(document, "lib")?;
    let bins = document
        .get("bin")
        .map(|v| v.as_array().ok_or("bin must be an array"))
        .transpose()?;
    let auto = package_edition != "2015" || (library.is_none() && bins.is_none());
    let mut candidates = Vec::new();
    if library.is_some() || flag(package, "autolib", auto)? {
        let empty = Table::new();
        let config = library.unwrap_or(&empty);
        let kinds = config
            .get("crate-type")
            .map(|v| v.as_array().ok_or("crate-type must be an array"))
            .transpose()?;
        if kinds.is_some_and(|kinds| kinds.iter().any(|kind| kind.as_str().is_none())) {
            return Err("crate-type entries must be strings".into());
        }
        if flag(config, "proc-macro", false)?
            || kinds
                .is_some_and(|kinds| kinds.iter().any(|kind| kind.as_str() == Some("proc-macro")))
        {
            note(
                notes,
                manifest,
                name,
                "procedural_macro_target_not_imported",
            )?;
        } else if kinds.is_some_and(|kinds| {
            !kinds
                .iter()
                .any(|kind| matches!(kind.as_str(), Some("lib" | "rlib" | "dylib")))
        }) {
            note(
                notes,
                manifest,
                name,
                "non_rust_library_target_not_imported",
            )?;
        } else {
            let root = relative(base, string(config, "path")?.unwrap_or("src/lib.rs"))?;
            let target_edition = if config.contains_key("edition") {
                edition(config, None)?
            } else {
                package_edition.clone()
            };
            candidates.push(Target {
                manifest: manifest.into(),
                name: crate_name(string(config, "name")?.unwrap_or(name))?,
                root,
                edition: target_edition,
                library: true,
            });
        }
    }
    let mut binaries = BTreeMap::<String, (String, String)>::new();
    if flag(package, "autobins", auto)? {
        let main = relative(base, "src/main.rs")?;
        if sources.contains(&main) {
            binaries.insert(name.into(), (main, package_edition.clone()));
        }
        let bin_base = relative(base, "src/bin")?;
        for source in sources {
            let Ok(tail) = Path::new(source).strip_prefix(&bin_base) else {
                continue;
            };
            let parts: Vec<_> = tail.components().collect();
            let bin_name = match parts.as_slice() {
                [Component::Normal(file)]
                    if Path::new(file).extension().is_some_and(|ext| ext == "rs") =>
                {
                    Path::new(file).file_stem().and_then(|s| s.to_str())
                }
                [Component::Normal(directory), Component::Normal(file)] if *file == "main.rs" => {
                    directory.to_str()
                }
                _ => None,
            };
            if let Some(bin_name) = bin_name {
                crate_name(bin_name)?;
                if binaries
                    .insert(bin_name.into(), (source.clone(), package_edition.clone()))
                    .is_some()
                {
                    return Err("ambiguous automatic binary target".into());
                }
            }
        }
    }
    if let Some(bins) = bins {
        if bins.len() > 16 {
            return Err("too many explicit binary targets".into());
        }
        let mut seen = BTreeSet::new();
        for config in bins {
            let config = config.as_table().ok_or("binary target must be a table")?;
            let bin_name = string(config, "name")?.ok_or("explicit binary target requires name")?;
            crate_name(bin_name)?;
            if !seen.insert(bin_name) {
                return Err("duplicate explicit binary name".into());
            }
            if let Some(required) = config.get("required-features") {
                let required = required
                    .as_array()
                    .ok_or("required-features must be an array")?;
                if required.len() > 128
                    || required.iter().any(|value| {
                        value
                            .as_str()
                            .is_none_or(|name| name.is_empty() || name.len() > 128)
                    })
                {
                    return Err("invalid required-features".into());
                }
                if !required.is_empty() {
                    binaries.remove(bin_name);
                    note(notes, manifest, bin_name, "required_features_not_imported")?;
                    continue;
                }
            }
            let root = if let Some(path) = string(config, "path")? {
                relative(base, path)?
            } else if let Some((root, _)) = binaries.get(bin_name) {
                root.clone()
            } else {
                let paths = [
                    format!("src/bin/{bin_name}.rs"),
                    format!("src/bin/{bin_name}/main.rs"),
                    "src/main.rs".into(),
                ];
                let matching = paths
                    .iter()
                    .enumerate()
                    .filter(|(index, _)| *index < 2 || bin_name == name)
                    .map(|(_, path)| relative(base, path))
                    .collect::<Result<Vec<_>>>()?
                    .into_iter()
                    .filter(|path| sources.contains(path))
                    .collect::<Vec<_>>();
                if matching.len() != 1 {
                    return Err(
                        "explicit binary path is missing or ambiguous in supplied sources".into(),
                    );
                }
                matching[0].clone()
            };
            let target_edition = if config.contains_key("edition") {
                edition(config, None)?
            } else {
                package_edition.clone()
            };
            binaries.insert(bin_name.into(), (root, target_edition));
        }
    }
    for (name, (root, edition)) in binaries {
        candidates.push(Target {
            manifest: manifest.into(),
            name,
            root,
            edition,
            library: false,
        });
    }
    let mut result = Vec::new();
    for target in candidates {
        if sources.contains(&target.root) {
            result.push(target);
        } else {
            note(notes, manifest, &target.root, "target_source_not_supplied")?;
        }
    }
    Ok(result)
}

pub(crate) fn import(
    workspace: &Workspace,
    manifests: &[String],
    files: &[String],
    crate_root: &str,
) -> Result<Value> {
    if manifests.is_empty() || manifests.len() > 16 || files.is_empty() || files.len() > 32 {
        return Err("manifest context requires 1..=16 manifests and 1..=32 Rust files".into());
    }
    let mut documents = BTreeMap::new();
    let mut manifest_hashes = BTreeMap::new();
    let mut source_hashes = BTreeMap::new();
    let mut file_names = BTreeMap::new();
    for path in manifests {
        if path.len() > 4096
            || Path::new(path)
                .file_name()
                .is_none_or(|name| name != "Cargo.toml")
        {
            return Err("manifest paths must name Cargo.toml and be at most 4096 bytes".into());
        }
        let (name, bytes) = workspace.read_source_bounded(Path::new(path), 64 * 1024)?;
        let source = String::from_utf8(bytes)?;
        let document = source.parse::<Table>()?;
        if document.contains_key("patch") || document.contains_key("replace") {
            return Err("patch/replace manifests require manually supplied crate context".into());
        }
        if documents.insert(name.clone(), document).is_some() {
            return Err("duplicate manifest path".into());
        }
        manifest_hashes.insert(name, blake3::hash(source.as_bytes()).to_hex().to_string());
    }
    for path in files {
        if path.len() > 4096 || Path::new(path).extension().is_none_or(|ext| ext != "rs") {
            return Err("explicit Rust source paths required".into());
        }
        let (name, bytes) = workspace.read_source_bounded(Path::new(path), 256 * 1024)?;
        std::str::from_utf8(&bytes)?;
        if source_hashes
            .insert(name.clone(), blake3::hash(&bytes).to_hex().to_string())
            .is_some()
        {
            return Err("duplicate Rust source path".into());
        }
        file_names.insert(path.as_str(), name);
    }
    let primary = file_names
        .get(crate_root)
        .ok_or("crate_root must be an explicit member of files")?;
    let sources = source_hashes.keys().cloned().collect::<BTreeSet<_>>();
    let workspaces = documents
        .iter()
        .filter(|(_, document)| document.contains_key("workspace"))
        .collect::<Vec<_>>();
    if workspaces.len() > 1 {
        return Err("multiple supplied Cargo workspaces are ambiguous".into());
    }
    let inherited = workspaces
        .first()
        .map(|(_, document)| table(document, "workspace"))
        .transpose()?
        .flatten();
    let inherited_base = workspaces
        .first()
        .and_then(|(path, _)| Path::new(path).parent());
    let mut notes = Vec::new();
    let mut all_targets = Vec::new();
    let mut workspace_members = BTreeSet::new();
    for (path, document) in &documents {
        let base = Path::new(path).parent().ok_or("invalid manifest parent")?;
        let mut inherits = inherited_base.is_some_and(|root| base.starts_with(root));
        if let Some(package) = table(document, "package")? {
            if let Some(declared) = string(package, "workspace")? {
                let base = Path::new(path).parent().ok_or("invalid manifest parent")?;
                let expected = relative(base, &format!("{declared}/Cargo.toml"))?;
                inherits = true;
                if workspaces
                    .first()
                    .is_none_or(|(path, _)| **path != expected)
                {
                    return Err(
                        "package workspace must match the supplied workspace manifest".into(),
                    );
                }
            }
            if document.contains_key("features") {
                note(
                    &mut notes,
                    path,
                    "features",
                    "feature_expansion_not_imported",
                )?;
            }
            if document.contains_key("target") {
                note(
                    &mut notes,
                    path,
                    "target",
                    "conditional_dependencies_not_imported",
                )?;
            }
        }
        if inherits {
            workspace_members.insert(path.clone());
        }
        all_targets.extend(targets(
            path,
            document,
            if inherits { inherited } else { None },
            &sources,
            &mut notes,
        )?);
    }
    all_targets.sort_by(|a, b| a.root.cmp(&b.root));
    if all_targets.is_empty() || all_targets.len() > 16 {
        return Err("manifest context requires 1..=16 supplied library/binary targets".into());
    }
    let libraries = all_targets
        .iter()
        .filter(|target| target.library)
        .map(|target| (target.manifest.as_str(), target))
        .collect::<BTreeMap<_, _>>();
    let mut crates = Vec::new();
    for target in &all_targets {
        let document = &documents[&target.manifest];
        let base = Path::new(&target.manifest)
            .parent()
            .ok_or("invalid manifest parent")?;
        let mut dependencies = BTreeMap::new();
        if !target.library
            && let Some(library) = libraries.get(target.manifest.as_str())
        {
            dependencies.insert(library.name.clone(), library.root.clone());
        }
        if let Some(declared) = table(document, "dependencies")? {
            if declared.len() > 128 {
                return Err("too many declared dependencies".into());
            }
            for (alias, value) in declared {
                crate_name(alias)?;
                let mut dependency_base = base;
                let mut value = value;
                let local = value.as_table();
                let optional = local
                    .map(|value| flag(value, "optional", false))
                    .transpose()?
                    .unwrap_or(false);
                if optional {
                    note(
                        &mut notes,
                        &target.manifest,
                        alias,
                        "optional_dependency_not_imported",
                    )?;
                    continue;
                }
                if local.is_some_and(|value| value.contains_key("workspace")) {
                    if !workspace_members.contains(&target.manifest) {
                        return Err(
                            "dependency inheritance requires a matching supplied workspace".into(),
                        );
                    }
                    let local = local.ok_or("invalid workspace dependency")?;
                    if !flag(local, "workspace", false)?
                        || local.keys().any(|key| {
                            !matches!(
                                key.as_str(),
                                "workspace" | "optional" | "features" | "default-features"
                            )
                        })
                    {
                        return Err("invalid inherited dependency settings".into());
                    }
                    value = inherited
                        .and_then(|w| w.get("dependencies"))
                        .and_then(toml::Value::as_table)
                        .and_then(|d| d.get(alias))
                        .ok_or("inherited dependency requires its supplied workspace definition")?;
                    dependency_base = inherited_base.ok_or("missing workspace manifest")?;
                }
                let Some(config) = value.as_table() else {
                    if !value.is_str() {
                        return Err("dependency must be a string or table".into());
                    }
                    note(
                        &mut notes,
                        &target.manifest,
                        alias,
                        "external_dependency_not_imported",
                    )?;
                    continue;
                };
                if config.contains_key("artifact") {
                    note(
                        &mut notes,
                        &target.manifest,
                        alias,
                        "artifact_dependency_not_imported",
                    )?;
                    continue;
                }
                let Some(path) = string(config, "path")? else {
                    note(
                        &mut notes,
                        &target.manifest,
                        alias,
                        "external_dependency_not_imported",
                    )?;
                    continue;
                };
                if config.contains_key("git") {
                    return Err("dependency cannot combine path and git".into());
                }
                let manifest = match relative(dependency_base, &format!("{path}/Cargo.toml")) {
                    Ok(path) => path,
                    Err(_) => {
                        note(
                            &mut notes,
                            &target.manifest,
                            alias,
                            "dependency_outside_workspace_not_imported",
                        )?;
                        continue;
                    }
                };
                let Some(library) = libraries.get(manifest.as_str()) else {
                    note(
                        &mut notes,
                        &target.manifest,
                        alias,
                        "dependency_library_not_supplied",
                    )?;
                    continue;
                };
                let package =
                    table(&documents[&manifest], "package")?.ok_or("dependency package missing")?;
                if string(package, "name")? != Some(string(config, "package")?.unwrap_or(alias)) {
                    return Err("path dependency package name mismatch".into());
                }
                let name = if config.contains_key("package") {
                    crate_name(alias)?
                } else {
                    library.name.clone()
                };
                if dependencies.insert(name, library.root.clone()).is_some() {
                    return Err("conflicting dependency crate names".into());
                }
                if config.contains_key("features")
                    || local.is_some_and(|value| value.contains_key("features"))
                {
                    note(
                        &mut notes,
                        &target.manifest,
                        alias,
                        "dependency_features_not_imported",
                    )?;
                }
            }
        }
        crates.push(CrateSpec {
            root: target.root.clone(),
            edition: Some(target.edition.clone()),
            dependencies,
            features: vec![],
            cfg_flags: vec![],
        });
    }
    let primary_edition = all_targets
        .iter()
        .find(|target| &target.root == primary)
        .ok_or("crate_root is not an imported library/binary target")?
        .edition
        .clone();
    let names = sources
        .iter()
        .map(|path| (path.as_str(), path.clone()))
        .collect();
    crate::lsp_project::graph(workspace.root(), &names, primary, &primary_edition, &crates)?;
    for (path, expected) in manifest_hashes.iter().chain(source_hashes.iter()) {
        let maximum = if manifest_hashes.contains_key(path) {
            64 * 1024
        } else {
            256 * 1024
        };
        let (normalized, bytes) = workspace.read_source_bounded(Path::new(path), maximum)?;
        if &normalized != path || blake3::hash(&bytes).to_hex().as_str() != expected {
            return Err("source or manifest changed during context import; retry".into());
        }
    }
    let report = json!({"analysis_arguments":{"files":sources,"crate_root":primary,"edition":primary_edition,"crates":crates},
        "manifest_hashes":manifest_hashes,"source_hashes":source_hashes,"omissions":notes,"complete":false,"compiler_checked":false,
        "limitations":["Only supplied manifests and Rust files are considered. No Cargo execution, filesystem discovery, downloads, build scripts or procedural macros.",
            "Only library/binary targets and unconditional non-optional supplied path dependencies are imported. Features, cfg flags, target-specific/dev/build dependencies and external crates are omitted.",
            "Returned arguments are a frozen explicit context, not a live Cargo project. Re-import after manifest changes; analysis endpoints do not automatically reread these manifests."]});
    if serde_json::to_vec(&report)?.len() > 128 * 1024 {
        return Err("manifest context report exceeds 128 KiB".into());
    }
    Ok(report)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture(entries: &[(&str, &str)]) -> (tempfile::TempDir, Workspace) {
        let directory = tempfile::tempdir().unwrap();
        for (path, text) in entries {
            let path = directory.path().join(path);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, text).unwrap();
        }
        let workspace = Workspace::new(directory.path()).unwrap();
        (directory, workspace)
    }

    fn run(workspace: &Workspace, manifests: &[&str], files: &[&str]) -> Result<Value> {
        import(
            workspace,
            &manifests.iter().map(|s| s.to_string()).collect::<Vec<_>>(),
            &files.iter().map(|s| s.to_string()).collect::<Vec<_>>(),
            files[0],
        )
    }

    #[test]
    fn imports_workspace_edges_and_deduplicates_omissions() {
        let (_directory, workspace) = fixture(&[
            (
                "Cargo.toml",
                "[workspace]\nmembers=['app','helper']\n[workspace.package]\nedition='2024'\n[workspace.dependencies]\nalias={package='helper',path='helper'}\n",
            ),
            (
                "app/Cargo.toml",
                "[package]\nname='app'\nedition.workspace=true\n[dependencies]\nalias.workspace=true\nexternal='1'\n",
            ),
            (
                "helper/Cargo.toml",
                "[package]\nname='helper'\nedition='2021'\n[lib]\nname='custom_helper'\n",
            ),
            ("app/src/main.rs", "fn main() {}"),
            ("app/src/lib.rs", "pub fn app() {}"),
            ("helper/src/lib.rs", "pub fn help() {}"),
        ]);
        let report = run(
            &workspace,
            &["Cargo.toml", "app/Cargo.toml", "helper/Cargo.toml"],
            &["app/src/main.rs", "app/src/lib.rs", "helper/src/lib.rs"],
        )
        .unwrap();
        let crates = report["analysis_arguments"]["crates"].as_array().unwrap();
        assert_eq!(crates.len(), 3);
        let main = crates
            .iter()
            .find(|c| c["root"] == "app/src/main.rs")
            .unwrap();
        assert_eq!(main["edition"], "2024");
        assert_eq!(main["dependencies"]["app"], "app/src/lib.rs");
        assert_eq!(main["dependencies"]["alias"], "helper/src/lib.rs");
        assert_eq!(
            report["omissions"]
                .as_array()
                .unwrap()
                .iter()
                .filter(|n| n["name"] == "external")
                .count(),
            1
        );
        assert_eq!(report["complete"], false);
    }

    #[test]
    fn unrelated_workspace_cannot_supply_inheritance() {
        let (_directory, workspace) = fixture(&[
            (
                "other/Cargo.toml",
                "[workspace]\n[workspace.package]\nedition='2024'",
            ),
            (
                "app/Cargo.toml",
                "[package]\nname='app'\nedition.workspace=true",
            ),
            ("app/src/lib.rs", ""),
        ]);
        assert!(
            run(
                &workspace,
                &["other/Cargo.toml", "app/Cargo.toml"],
                &["app/src/lib.rs"]
            )
            .unwrap_err()
            .to_string()
            .contains("inherited edition")
        );
    }

    #[test]
    fn rejects_unsupported_primary_and_unsafe_inputs() {
        for manifest in [
            "[package]\nname='app'\n[lib]\nproc-macro=true",
            "[package]\nname='app'\n[lib]\ncrate-type=['proc-macro']",
            "[package]\nname='app'\n[lib]\ncrate-type=['cdylib']",
            "[package]\nname='app'\n[patch.crates-io]\nx={path='x'}",
        ] {
            let (_directory, workspace) = fixture(&[("Cargo.toml", manifest), ("src/lib.rs", "")]);
            assert!(run(&workspace, &["Cargo.toml"], &["src/lib.rs"]).is_err());
        }
        let (_directory, workspace) =
            fixture(&[("Cargo.toml", "[package]\nname='app'"), ("src/lib.rs", "")]);
        assert!(run(&workspace, &["../Cargo.toml"], &["src/lib.rs"]).is_err());
        assert!(run(&workspace, &["Cargo.toml", "./Cargo.toml"], &["src/lib.rs"]).is_err());
        assert!(run(&workspace, &["Cargo.toml"], &["src/lib.rs", "./src/lib.rs"]).is_err());
    }

    #[test]
    fn automatic_targets_follow_edition_and_explicit_settings() {
        for (edition, autobins, count) in [
            ("2015", "", 1),
            ("2024", "", 2),
            ("2024", "autobins=false", 1),
        ] {
            let manifest = format!(
                "[package]\nname='app'\nedition='{edition}'\n{autobins}\n[lib]\npath='src/lib.rs'"
            );
            let (_directory, workspace) = fixture(&[
                ("Cargo.toml", &manifest),
                ("src/lib.rs", ""),
                ("src/main.rs", "fn main() {}"),
            ]);
            let report = run(&workspace, &["Cargo.toml"], &["src/lib.rs", "src/main.rs"]).unwrap();
            assert_eq!(
                report["analysis_arguments"]["crates"]
                    .as_array()
                    .unwrap()
                    .len(),
                count
            );
        }
    }
    #[test]
    fn cyclic_and_mismatched_path_dependencies_reject() {
        for dependency in ["a={path='../a'}", "wrong={path='../a'}"] {
            let second =
                format!("[package]\nname='b'\nedition='2024'\n[dependencies]\n{dependency}");
            let (_directory, workspace) = fixture(&[
                (
                    "a/Cargo.toml",
                    "[package]\nname='a'\nedition='2024'\n[dependencies]\nb={path='../b'}",
                ),
                ("b/Cargo.toml", &second),
                ("a/src/lib.rs", ""),
                ("b/src/lib.rs", ""),
            ]);
            assert!(
                run(
                    &workspace,
                    &["a/Cargo.toml", "b/Cargo.toml"],
                    &["a/src/lib.rs", "b/src/lib.rs"]
                )
                .is_err()
            );
        }
    }
}
