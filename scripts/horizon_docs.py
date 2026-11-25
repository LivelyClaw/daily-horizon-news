"""将每期编辑稿导入飞书归档文件夹，核对只读权限后发送速览。"""
import hashlib
import json
import re
import time
from itertools import groupby
from urllib.parse import urljoin

import httpx
from dotenv import dotenv_values

if __package__:
    from .horizon_webhook import FeishuWebhook
else:
    from horizon_webhook import FeishuWebhook


FOLDER = "Horizon 中文日报"
PUBLIC_READ = {"external_access_entity": "open", "link_share_entity": "anyone_readable",
               "comment_entity": "anyone_can_edit", "manage_collaborator_entity": "collaborator_full_access"}


class RejectedRequest(ValueError):
    """飞书明确拒绝了请求，可以重试。"""


class Drive:
    def __init__(self, env_file):
        self.http = httpx.Client(base_url="https://open.feishu.cn/open-apis/", timeout=40)
        with env_file.open(encoding="utf-8") as stream:
            config = dotenv_values(stream=stream, interpolate=False)
        credentials = {"app_id": config.get("FEISHU_APP_ID"), "app_secret": config.get("FEISHU_APP_SECRET")}
        if not all(credentials.values()):
            raise ValueError("Hermes .env 缺少飞书应用凭据")
        result = self.request("POST", "auth/v3/tenant_access_token/internal", json=credentials)
        self.http.headers["Authorization"] = "Bearer " + result["tenant_access_token"]

    def request(self, method, path, **kwargs):
        try:
            response = self.http.request(method, path, **kwargs)
            result = response.json()
        except (httpx.HTTPError, ValueError):
            raise ValueError("飞书云文档接口未返回有效回执") from None
        if not isinstance(result, dict) or result.get("code") != 0 or not response.is_success:
            code = result.get("code") if isinstance(result, dict) else None
            error = RejectedRequest if response.status_code < 500 and isinstance(code, int) and code else ValueError
            raise error(f"飞书云文档接口失败（HTTP {response.status_code}，错误码 {code}）")
        return result.get("data", result)

    def find(self, parent, name, kind):
        params = {"folder_token": parent, "page_size": 200}
        matches = []
        while True:
            data = self.request("GET", "drive/v1/files", params=params)
            matches.extend(f for f in data["files"] if f["name"] == name and f["type"] == kind)
            if not data["has_more"]:
                break
            params["page_token"] = data["next_page_token"]
        if len(matches) > 1:
            raise ValueError(f"飞书目录内有多个同名项目：{name}")
        return matches[0] if matches else None

    def folder(self):
        root = self.request("GET", "drive/explorer/v2/root_folder/meta")["token"]
        folder = self.find(root, FOLDER, "folder")
        if not folder:
            folder = self.request("POST", "drive/v1/files/create_folder",
                                  json={"folder_token": root, "name": FOLDER})
        return folder["token"]

    def upload(self, title, markdown):
        # 飞书 Markdown 导入不支持 HTML 折叠标签，保留其中的正文和引用链接。
        text = re.sub(r"<details>\s*<summary>(.*?)</summary>", r"\n\n**\1**\n\n", markdown, flags=re.I | re.S)
        text = re.sub(r"</?details>|<a id=\"[^\"]+\"></a>", "", text, flags=re.I)
        body = text.encode("utf-8")
        name = title + ".md"
        data = self.request("POST", "drive/v1/medias/upload_all",
                            data={"file_name": name, "parent_type": "ccm_import_open", "size": str(len(body)),
                                  "extra": json.dumps({"obj_type": "docx", "file_extension": "md"})},
                            files={"file": (name, body, "text/markdown")})
        return data["file_token"]

    def public_read(self, token, kind="docx"):
        path = f"drive/v2/permissions/{token}/public"
        params = {"type": kind}
        settings = self.request("GET", path, params=params)["permission_public"]
        if kind == "folder":
            if settings.get("external_access_entity") != "open" or settings.get("link_share_entity") != "anyone_readable":
                raise ValueError(f"请在飞书分享面板将文件夹 {FOLDER} 设为「互联网获得链接的人可阅读」")
            return
        if all(settings.get(key) == value for key, value in PUBLIC_READ.items()):
            return
        self.request("PATCH", path, params=params, json=PUBLIC_READ)
        settings = self.request("GET", path, params=params)["permission_public"]
        if not all(settings.get(key) == value for key, value in PUBLIC_READ.items()):
            raise ValueError("飞书未确认文档已开启持链接只读")


def configure_folder(db, env_file):
    FeishuWebhook.from_file(env_file)
    drive = Drive(env_file)
    try:
        with db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT token FROM doc_folder WHERE id=1").fetchone()
            folder = row[0] if row else drive.folder()
            if not row:
                db.execute("INSERT INTO doc_folder VALUES (1, ?)", (folder,))
        drive.public_read(folder, "folder")
        return folder
    finally:
        drive.http.close()


def import_document(db, drive, folder, row_id, title, markdown):
    ticket, token, url = db.execute(
        "SELECT ticket, token, url FROM publications WHERE edition=?", (row_id,)).fetchone()
    if token:
        return token, url
    if not ticket:
        upload = drive.upload(title, markdown)
        with db:
            db.execute("UPDATE publications SET ticket='submitting' WHERE edition=?", (row_id,))
        try:
            result = drive.request("POST", "drive/v1/import_tasks", json={
                "file_extension": "md", "file_token": upload, "type": "docx", "file_name": title,
                "point": {"mount_type": 1, "mount_key": folder}})
        except RejectedRequest:
            with db:
                db.execute("UPDATE publications SET ticket=NULL WHERE edition=?", (row_id,))
            raise
        ticket = result["ticket"]
        with db:
            db.execute("UPDATE publications SET ticket=? WHERE edition=?", (ticket, row_id))
    if ticket == "submitting":
        # 如果建文回执丢失，只认领目标文件夹内的同名文档，避免重复创建。
        doc = drive.find(folder, title, "docx")
        if not doc:
            raise ValueError("飞书建文回执不明确，等待目标文件夹出现本期文档")
        token, url = doc["token"], doc["url"]
    else:
        for _ in range(10):
            doc = drive.request("GET", "drive/v1/import_tasks/" + ticket)["result"]
            status = doc["job_status"]
            if status not in (1, 2):
                break
            time.sleep(1)
        if status in (1, 2):
            raise ValueError("飞书仍在导入，下一轮继续查询同一任务")
        if status != 0:
            with db:
                db.execute("UPDATE publications SET ticket=NULL WHERE edition=?", (row_id,))
            raise ValueError(f"飞书导入失败，状态码 {status}")
        if set(doc.get("extra") or []) - {"_pod_name"}:
            raise ValueError("飞书提示导入可能不完整，请检查本期文档")
        token, url = doc["token"], doc["url"]
    with db:
        db.execute("UPDATE publications SET token=?,url=? WHERE edition=?", (token, url, row_id))
    return token, url


def preview_lines(lines):
    for line in lines:
        while len(json.dumps(line, ensure_ascii=False).encode()) > 16500:
            # 单个来源名也可能很长；每段 2500 个字符即使全需 JSON 转义也能放入一张卡片。
            yield line[:2500]
            line = line[2500:]
        yield line


def card_message(title, elements, url, folder_url, *, last):
    if last:
        elements = [*elements, {"tag": "action", "actions": [
            {"tag": "button", "type": "primary", "text": {"tag": "plain_text", "content": "查看本期完整日报"}, "url": url},
            {"tag": "button", "text": {"tag": "plain_text", "content": "浏览历期日报"}, "url": folder_url}]}]
    return {"msg_type": "interactive", "card": {"header": {
        "template": "blue", "title": {"tag": "plain_text", "content": title}}, "elements": elements}}


# 飞书 Markdown 支持用 HTML 字符实体显示字面字符，包括标签、链接和强调标记。
MD_LITERAL = str.maketrans({c: f"&#{ord(c)};" for c in "&<>*_~`[]!\\|$#"})


def topic_cards(title, topics, url, folder_url):
    count = sum(len(topic["events"]) for topic in topics)
    counts = {topic["name"]: len(topic["events"]) for topic in topics}
    summary = f"{count} 个事件 · {len(topics)} 个主题"

    def elements_for(rows):
        elements = [{"tag": "div", "text": {"tag": "plain_text", "content": summary}}]
        for name, members in groupby(rows, key=lambda row: row[0]):
            if len(elements) > 1:
                elements.append({"tag": "hr"})
            heading = f"**{name.translate(MD_LITERAL)}**  <font color='grey'>{counts[name]} 条</font>"
            lines = [heading, *(line for _, line in members)]
            elements.append({"tag": "markdown", "content": "\n".join(lines)})
        return elements

    pages, page = [], []
    for topic in topics:
        name = topic["name"]
        for event in topic["events"]:
            before, focus, after = (event[key].translate(MD_LITERAL) for key in ("before", "focus", "after"))
            lead = f"<font color='grey'>{event['number']:02}</font>  {before}**{focus}**{after}"
            source = event["source"]
            # 异常长的订阅源名称也要完整保留。每段在最坏的字符转义后仍小于单卡上限。
            for offset in range(0, max(1, len(source)), 1200):
                part = source[offset:offset + 1200].translate(MD_LITERAL)
                label = lead if offset == 0 else f"{event['number']:02} 来源（续）"
                row = (name, f"{label} <font color='grey'>（{part}）</font>")
                candidate = card_message(title, elements_for([*page, row]), url, folder_url, last=True)
                size = len(json.dumps(candidate, ensure_ascii=False, separators=(",", ":")).encode())
                if page and size > 19_500:  # 签名、时间戳和分页标题的余量
                    pages.append(page)
                    page = []
                page.append(row)
    if page:
        pages.append(page)
    for index, rows in enumerate(pages, 1):
        header = title if len(pages) == 1 else f"{title}（{index}/{len(pages)}）"
        yield card_message(header, elements_for(rows), url, folder_url, last=index == len(pages))


def cards(title, preview, url, folder_url, topics=None):
    if topics:
        yield from topic_cards(title, topics, url, folder_url)
        return
    lines = preview.splitlines()
    lines = [line for line in lines if line and line != "完整报道见本期 Markdown 附件。"]
    if lines and lines[0].startswith("Horizon 中文日报 · "):
        lines[0] = lines[0].partition("｜")[2].replace("本期原始 Markdown 见附件。", "详见本期完整日报。").replace(
            "详见 Markdown 附件。", "详见本期完整日报。").replace("详见附件。", "详见本期完整日报。")
    # 为卡片结构、URL 和签名预留空间，按完整摘要行拆页。
    pages, page, size = [], [], 0
    for line in preview_lines(lines):
        n = len(json.dumps(line, ensure_ascii=False).encode()) + 2
        if page and size+n > 17000:
            pages.append(page)
            page, size = [], 0
        page.append(line)
        size += n
    pages.append(page)
    for i, group in enumerate(pages, 1):
        header = f"{title}（{i}/{len(pages)}）" if len(pages) > 1 else title
        elements = [{"tag": "div", "text": {"tag": "plain_text", "content": "\n".join(group)}}]
        yield card_message(header, elements, url, folder_url, last=i == len(pages))


def publish(db, data_dir, row_id, day, digest, preview, markdown, env_file, *, topics=None):
    hook = FeishuWebhook.from_file(env_file)
    folder = db.execute("SELECT token FROM doc_folder WHERE id=1").fetchone()[0]
    title, = db.execute("SELECT edition_name FROM editions WHERE rowid=?", (row_id,)).fetchone()
    if not title:
        title = f"{day} 日报｜{FOLDER}（{digest.hex()[:8]}）"
    with db:
        db.execute("INSERT OR IGNORE INTO publications (edition,title) VALUES (?,?)", (row_id, title))
    title, = db.execute("SELECT title FROM publications WHERE edition=?", (row_id,)).fetchone()
    archive = data_dir / "editions"
    archive.mkdir(exist_ok=True)
    dest = archive / f"{title}.md"
    temp = dest.with_suffix(".tmp")
    temp.write_text(markdown, encoding="utf-8")
    temp.replace(dest)
    drive = Drive(env_file)
    try:
        drive.public_read(folder, "folder")
        token, url = import_document(db, drive, folder, row_id, title, markdown)
        drive.public_read(token)
    finally:
        drive.http.close()
    route = hashlib.sha256(hook.url.encode()).hexdigest()
    with db:
        db.execute("UPDATE publications SET route=?,next_page=0 WHERE edition=? AND route IS NOT ?",
                   (route, row_id, route))
    start, = db.execute("SELECT next_page FROM publications WHERE edition=?", (row_id,)).fetchone()
    folder_url = urljoin(url, "/drive/folder/" + folder)
    for index, page in enumerate(cards(title, preview, url, folder_url, topics=topics)):
        if index < start:
            continue
        hook.send(page)
        with db:
            db.execute("UPDATE publications SET next_page=? WHERE edition=?", (index+1, row_id))
    with db:
        db.execute("UPDATE editions SET body=NULL,attachment=NULL,phase='done' WHERE rowid=?", (row_id,))
