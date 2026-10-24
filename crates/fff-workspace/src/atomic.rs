//! Atomic replacement shared by direct workspace writes and journal replay.
use std::fs;
use std::io::{self, Write};
use std::path::Path;

pub(crate) fn replace_file(
    path: &Path,
    temp: &Path,
    bytes: &[u8],
    recreate_mode: Option<u32>,
) -> io::Result<Option<u32>> {
    let permissions = match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.is_file() => Some(metadata.permissions()),
        Ok(_) => {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "replacement target must be a regular file",
            ));
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                recreate_mode.map(|mode| fs::Permissions::from_mode(mode & 0o777))
            }
            #[cfg(not(unix))]
            {
                let _ = recreate_mode;
                None
            }
        }
        Err(error) => return Err(error),
    };
    let mut options = fs::OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    if let Some(permissions) = &permissions {
        use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
        // Private destinations must not expose bytes before chmod runs.
        options.mode(permissions.mode() & 0o7777);
    }
    // create_new rejects existing files, hard links, symlinks and directories.
    // Never remove a collision: only clean up a temporary file we created.
    let mut file = options.open(temp)?;
    let result = (|| {
        if let Some(permissions) = permissions {
            file.set_permissions(permissions)?;
        }
        file.write_all(bytes)?;
        #[cfg(unix)]
        let mode = {
            use std::os::unix::fs::PermissionsExt;
            Some(file.metadata()?.permissions().mode() & 0o777)
        };
        #[cfg(not(unix))]
        let mode = None;
        drop(file);
        fs::rename(temp, path)?;
        Ok(mode)
    })();
    if result.is_err() {
        let _ = fs::remove_file(temp);
    }
    result
}
