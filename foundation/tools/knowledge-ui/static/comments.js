// Same-origin iframe access keeps selection text out of cross-window messages.
const commentsPanel = document.getElementById("comments-panel");
const commentSelection = document.getElementById("comment-selection");
const commentForm = document.getElementById("comment-form");
const commentBody = document.getElementById("comment-body");
const commentList = document.getElementById("comment-list");
const commentError = document.getElementById("comment-error");
let commentsFile = "";
let selectedPassage = null;
let commentsRequest = 0;

async function commentsApi(url, options) {
  const response = await fetch(url, options);
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Comment request failed");
  return result;
}

function showComments(items) {
  commentList.replaceChildren();
  if (!items.length) commentList.textContent = "コメントはありません。記事の文章を選択すると追加できます。";
  for (const item of items) {
    const card = document.createElement("article");
    const heading = document.createElement("button");
    heading.type = "button";
    heading.textContent = `${item.status} · ${item.heading || "記事冒頭"}`;
    heading.onclick = () => {
      const doc = articleFrame.contentDocument;
      const target = item.anchor ? doc.getElementById(item.anchor.replace(/^#/, "")) : doc.body;
      if (target) target.scrollIntoView({behavior: "smooth"});
    };
    const quote = document.createElement("blockquote");
    quote.textContent = item.quote;
    const body = document.createElement("p");
    body.textContent = item.body;
    const created = document.createElement("small");
    created.textContent = item.created;
    const status = document.createElement("button");
    status.type = "button";
    status.textContent = item.status === "open" ? "解決" : "再開";
    status.onclick = () => changeComment(item.id, item.status === "open" ? "resolved" : "open");
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "削除";
    remove.onclick = () => {
      if (window.confirm("このコメントを削除しますか？")) changeComment(item.id);
    };
    card.append(heading, quote, body, created, status, remove);
    commentList.append(card);
  }
}

async function refreshComments() {
  const request = ++commentsRequest;
  const file = commentsFile;
  try {
    const data = await commentsApi(`/api/comments?file=${encodeURIComponent(file)}`);
    if (request === commentsRequest && file === commentsFile) showComments(data.comments);
  } catch (error) {
    if (request === commentsRequest) commentError.textContent = error.message;
  }
}

async function changeComment(id, status) {
  const file = commentsFile;
  commentError.textContent = "";
  try {
    const url = `/api/comments/${encodeURIComponent(id)}`;
    if (status) {
      await commentsApi(url, {method: "PATCH", headers: {"Content-Type": "application/json"}, body: JSON.stringify({file, status})});
    } else {
      await commentsApi(`${url}?file=${encodeURIComponent(file)}`, {method: "DELETE"});
    }
    if (file === commentsFile) await refreshComments();
  } catch (error) {
    if (file === commentsFile) commentError.textContent = error.message;
  }
}

commentSelection.onclick = () => {
  commentForm.hidden = false;
  commentBody.focus();
};
document.getElementById("comment-cancel").onclick = () => { commentForm.hidden = true; };
commentForm.onsubmit = async (event) => {
  event.preventDefault();
  if (!selectedPassage) return;
  const file = commentsFile;
  const submit = commentForm.querySelector('[type="submit"]');
  submit.disabled = true;
  commentError.textContent = "";
  try {
    await commentsApi("/api/comments", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({file, ...selectedPassage, body: commentBody.value})});
    if (file === commentsFile) {
      commentBody.value = "";
      commentForm.hidden = true;
      await refreshComments();
    }
  } catch (error) {
    if (file === commentsFile) commentError.textContent = error.message;
  } finally {
    submit.disabled = false;
  }
};

articleFrame.addEventListener("load", () => {
  ++commentsRequest;
  commentsFile = "";
  selectedPassage = null;
  commentsPanel.hidden = true;
  commentSelection.hidden = true;
  commentForm.hidden = true;
  commentBody.value = "";
  commentError.textContent = "";
  commentList.replaceChildren();
  const url = new URL(articleFrame.contentWindow.location.href);
  const file = url.searchParams.get("file") || "";
  if (url.pathname !== "/article" || !file.toLowerCase().endsWith(".md")) return;
  commentsFile = file;
  commentsPanel.hidden = false;
  refreshComments();
  const doc = articleFrame.contentDocument;
  doc.addEventListener("selectionchange", () => {
    // Keep the captured passage stable while composing a comment.
    if (!commentForm.hidden) return;
    const selection = doc.getSelection();
    const quote = selection?.toString().trim();
    commentSelection.hidden = !quote;
    if (!quote || !selection.rangeCount) return;
    const start = selection.getRangeAt(0).startContainer;
    let heading = null;
    for (const candidate of doc.querySelectorAll("h1,h2,h3,h4,h5,h6")) {
      if (candidate.contains(start) || (candidate.compareDocumentPosition(start) & Node.DOCUMENT_POSITION_FOLLOWING)) heading = candidate;
    }
    selectedPassage = {quote, heading: heading?.textContent || "", anchor: heading?.id || ""};
  });
});
