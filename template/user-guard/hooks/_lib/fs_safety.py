"""
fs_safety.py - Shared filesystem safety, atomic I/O, bounded JSONL reading, and session lock primitives.
Pure Python standard library only.
"""

import contextlib
import errno
import os
import re
import stat
import time
import uuid
from pathlib import Path
from typing import Any, Iterator, List, Tuple

CODEX_META_HEAD_BYTES = 65536
CODEX_META_MAX_LINE_BYTES = 8 * 1024 * 1024
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def valid_session_id(session_id: Any) -> bool:
    return isinstance(session_id, str) and bool(_SESSION_ID_RE.fullmatch(session_id))


def decode_jsonl_line(raw: bytes) -> str:
    """只去掉行首 BOM；严格解码，避免把坏字节替换后误报为完整原话。"""
    return raw.decode("utf-8-sig").strip()


def read_bounded_first_line(
    rollout_path: Path, max_bytes: int = CODEX_META_MAX_LINE_BYTES
) -> Tuple[bytes, bool]:
    """有界流式按块读取文件首行至首个 \n，返回 (first_line_bytes, exceeded_limit)。"""
    chunks: List[bytes] = []
    total = 0
    with open(rollout_path, "rb") as f:
        while total <= max_bytes:
            need = min(CODEX_META_HEAD_BYTES, max_bytes - total + 1)
            buf = f.read(need)
            if not buf:
                break
            nl = buf.find(b"\n")
            if nl != -1:
                chunks.append(buf[: nl + 1])
                return b"".join(chunks), False
            chunks.append(buf)
            total += len(buf)
            if total > max_bytes:
                return b"", True
    return b"".join(chunks), False


_IO_REPARSE_TAG_SYMLINK = getattr(stat, "IO_REPARSE_TAG_SYMLINK", 0xA000000C)
_IO_REPARSE_TAG_MOUNT_POINT = getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003)


def is_reparse_or_link(path: Path) -> bool:
    try:
        st = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if not (getattr(st, "st_file_attributes", 0) & reparse_flag):
        return False
    tag = getattr(st, "st_reparse_tag", None)
    if tag is None:
        return True
    return tag in (_IO_REPARSE_TAG_SYMLINK, _IO_REPARSE_TAG_MOUNT_POINT)


def _unlock_and_close_fd(fd: int, locked: bool) -> None:
    if fd >= 0:
        if locked:
            with contextlib.suppress(OSError):
                if os.name == "nt":
                    import msvcrt

                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_UN)
        with contextlib.suppress(OSError):
            os.close(fd)


def atomic_write_text(target_path: Path, content: str) -> None:
    """使用同目录临时文件 + fsync + os.replace 原子更新文件，防止并发或中断产生半截损坏文件"""
    target_abs = Path(os.path.abspath(str(target_path)))
    if is_reparse_or_link(target_path) or is_reparse_or_link(target_abs):
        raise OSError(errno.EINVAL, "拒绝通过符号链接或重解析点写入状态文件", str(target_path))
    parent_abs = target_abs.parent
    for anc in (target_path.parent, parent_abs, *parent_abs.parents):
        if is_reparse_or_link(anc):
            raise OSError(errno.EINVAL, "拒绝通过符号链接或重解析点写入状态文件", str(anc))
    target_path.parent.mkdir(parents=True, exist_ok=True)
    for anc in (target_path.parent, parent_abs, *parent_abs.parents):
        if is_reparse_or_link(anc):
            raise OSError(errno.EINVAL, "状态文件父目录或其祖先目录不得为符号链接或重解析点", str(anc))
    if is_reparse_or_link(target_path) or is_reparse_or_link(target_abs):
        raise OSError(errno.EINVAL, "拒绝通过符号链接或重解析点写入状态文件", str(target_path))
    tmp_path = target_path.with_name(f".{target_path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}")
    created = False
    try:
        tmp_fd = os.open(
            tmp_path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        created = True
        try:
            f_obj = os.fdopen(tmp_fd, "w", encoding="utf-8", newline="\n")
        except Exception:
            with contextlib.suppress(OSError):
                os.close(tmp_fd)
            raise
        with f_obj as f:
            f.write(content)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError as exc:
                if exc.errno not in (errno.EINVAL, errno.ENOSYS):
                    raise
        for attempt in range(6):
            if is_reparse_or_link(target_path) or is_reparse_or_link(target_abs):
                raise OSError(errno.EINVAL, "拒绝通过符号链接或重解析点写入状态文件", str(target_path))
            try:
                os.replace(tmp_path, target_path)
                break
            except PermissionError:
                if attempt >= 5:
                    raise
                time.sleep(0.01 * (2 ** attempt))
    finally:
        try:
            if created:
                tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


@contextlib.contextmanager
def session_state_lock(state_dir: Path, session_id: str, timeout_sec: float = 1.5) -> Iterator[None]:
    """串行化同会话状态事务。锁文件作为稳定锚点，进程退出由内核释放锁。"""
    if not valid_session_id(session_id):
        raise ValueError("会话标识缺失或不合法")
    state_dir_abs = Path(os.path.abspath(str(state_dir)))
    for anc in (state_dir_abs, *state_dir_abs.parents):
        if is_reparse_or_link(anc):
            raise OSError(errno.EINVAL, "状态目录或其父目录不得为符号链接或重解析点", str(anc))
    state_dir.mkdir(parents=True, exist_ok=True)
    for anc in (state_dir_abs, *state_dir_abs.parents):
        if is_reparse_or_link(anc):
            raise OSError(errno.EINVAL, "状态目录或其父目录不得为符号链接或重解析点", str(anc))
    path = state_dir / f".{session_id}.lock"
    deadline = time.monotonic() + timeout_sec
    token = uuid.uuid4().hex.encode("ascii")
    fd = -1
    locked = False
    lock_busy_errnos = (errno.EACCES, errno.EAGAIN, getattr(errno, "EWOULDBLOCK", errno.EAGAIN), errno.EDEADLK)

    def _unlock_and_close_current() -> None:
        nonlocal fd, locked
        _unlock_and_close_fd(fd, locked)
        fd = -1
        locked = False

    try:
        while True:
            if is_reparse_or_link(path):
                raise OSError(errno.EINVAL, "会话锁不得为符号链接或重解析点", str(path))
            try:
                fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
            except OSError as exc:
                retryable = exc.errno == errno.ENOENT or (os.name == "nt" and exc.errno == errno.EACCES)
                if retryable and time.monotonic() < deadline:
                    time.sleep(0.005)
                    continue
                raise
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink > 1:
                raise OSError(errno.EINVAL, "会话锁必须为独立普通文件", str(path))
            try:
                anchor = path.lstat()
            except FileNotFoundError:
                anchor = None
            if anchor is not None and (not stat.S_ISREG(anchor.st_mode) or is_reparse_or_link(path) or anchor.st_nlink > 1):
                raise OSError(errno.EINVAL, "会话锁必须为独立普通文件", str(path))
            if anchor is None or opened.st_nlink != 1 or (anchor.st_dev, anchor.st_ino) != (opened.st_dev, opened.st_ino):
                _unlock_and_close_current()
                if time.monotonic() >= deadline:
                    raise TimeoutError("未取得会话状态锁，本次未更新状态")
                time.sleep(0.005)
                continue

            while True:
                try:
                    if os.name == "nt":
                        import msvcrt

                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                    break
                except OSError as exc:
                    if exc.errno not in lock_busy_errnos:
                        raise
                    if time.monotonic() >= deadline:
                        raise TimeoutError("未取得会话状态锁，本次未更新状态") from exc
                    time.sleep(0.005)

            opened_after = os.fstat(fd)
            try:
                anchor_after = path.lstat()
            except FileNotFoundError:
                anchor_after = None
            if anchor_after is not None and (
                not stat.S_ISREG(anchor_after.st_mode)
                or is_reparse_or_link(path)
                or opened_after.st_nlink > 1
                or anchor_after.st_nlink > 1
            ):
                raise OSError(errno.EINVAL, "会话锁必须为独立普通文件", str(path))
            if (
                anchor_after is None
                or opened_after.st_nlink != 1
                or (anchor_after.st_dev, anchor_after.st_ino) != (opened_after.st_dev, opened_after.st_ino)
            ):
                _unlock_and_close_current()
                if time.monotonic() >= deadline:
                    raise TimeoutError("未取得会话状态锁，本次未更新状态")
                time.sleep(0.005)
                continue
            break

        os.lseek(fd, 1, os.SEEK_SET)
        os.write(fd, token)
        try:
            yield
        finally:
            # 清理锁内元数据失败也要关闭描述符，且不能遮盖事务本身的异常。
            with contextlib.suppress(OSError):
                os.lseek(fd, 1, os.SEEK_SET)
                if os.read(fd, len(token)) == token:
                    os.ftruncate(fd, 1)
    finally:
        _unlock_and_close_fd(fd, locked)


def try_unlink_unheld_lock(lock_path: Path) -> bool:
    """仅在确认无进程持有文件锁时删除陈旧 .lock 锚点，避免与活跃会话发生竞态。"""
    if is_reparse_or_link(lock_path):
        return False
    fd = -1
    locked = False
    st = None
    verified = False
    try:
        fd = os.open(lock_path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            return False
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            locked = True
            cur_st = lock_path.lstat()
            if (
                not stat.S_ISREG(cur_st.st_mode)
                or is_reparse_or_link(lock_path)
                or cur_st.st_nlink != 1
                or (cur_st.st_dev, cur_st.st_ino) != (st.st_dev, st.st_ino)
            ):
                return False
            verified = True
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
            cur_st = lock_path.lstat()
            if (
                not stat.S_ISREG(cur_st.st_mode)
                or is_reparse_or_link(lock_path)
                or cur_st.st_nlink != 1
                or (cur_st.st_dev, cur_st.st_ino) != (st.st_dev, st.st_ino)
            ):
                return False
            lock_path.unlink()
            return True
    except OSError:
        return False
    finally:
        _unlock_and_close_fd(fd, locked)
    if os.name == "nt" and locked and verified and st is not None:
        try:
            cur_st = lock_path.lstat()
            if (
                stat.S_ISREG(cur_st.st_mode)
                and not is_reparse_or_link(lock_path)
                and cur_st.st_nlink == 1
                and (cur_st.st_dev, cur_st.st_ino) == (st.st_dev, st.st_ino)
            ):
                lock_path.unlink()
                return True
        except OSError:
            return False
    return False
