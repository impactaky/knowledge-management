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
let reviewState = null;
const reviewElement = (id) => document.getElementById(`review-${id}`);

function saveComposition() {
  if (!commentsFile) return;
  try { localStorage.setItem(`knowledge-comment:${commentsFile}`, JSON.stringify({passage: selectedPassage, body: commentBody.value})); } catch (_) { /* Storage may be disabled. */ }
}
commentBody.addEventListener('input', saveComposition);
function restoreComposition() {
  try {
    const saved = JSON.parse(localStorage.getItem(`knowledge-comment:${commentsFile}`) || 'null');
    if (saved?.passage && typeof saved.body === 'string') {
      selectedPassage = saved.passage;
      commentBody.value = saved.body;
      commentForm.hidden = false;
    }
  } catch (_) { /* A missing or corrupt draft does not prevent commenting. */ }
}


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
    const reply = reviewState?.reply?.orders?.find(order => order.comment_id === item.id);
    if (reply?.reason && ['partial', 'skipped'].includes(reply.status)) {
      const reason = document.createElement('p');
      reason.textContent = `${reply.status}: ${reply.reason}`;
      card.append(reason);
    }
    if (reply?.needsConfirm) {
      const details = document.createElement('details');
      const marker = document.createElement('summary');
      marker.textContent = '? 確認したいこと';
      const question = document.createElement('p');
      question.textContent = reply.question;
      const answer = document.createElement('textarea');
      answer.setAttribute('aria-label', '問いへの答え');
      const key = `knowledge-answer:${commentsFile}:${item.id}`;
      try { answer.value = localStorage.getItem(key) || ''; } catch (_) {}
      answer.oninput = () => { try { localStorage.setItem(key, answer.value); } catch (_) {} };
      const send = document.createElement('button');
      send.textContent = '答えを保存';
      send.onclick = async () => {
        const file = commentsFile;
        try {
          await commentsApi('/api/comments', {method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({file, quote: item.quote || '(記事全体)', heading: item.heading, anchor: item.anchor,
              body: answer.value, reply_to: item.id})});
          try { localStorage.removeItem(key); } catch (_) {}
          if (file === commentsFile) await refreshComments();
        } catch (error) { commentError.textContent = error.message; }
      };
      details.append(marker, question, answer, send);
      card.append(details);
    }
    if (item.reply_to) {
      const relation = document.createElement('small');
      relation.textContent = `返信先: ${item.reply_to}`;
      card.append(relation);
    }
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
      try { localStorage.removeItem(`knowledge-comment:${file}`); } catch (_) {}
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
  saveComposition();
  ++commentsRequest;
  reviewState = null;
  reviewElement('diff').hidden = true;
  articleFrame.hidden = false;
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
  restoreComposition();
  refreshComments();
  refreshReview();
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
    saveComposition();
  });
});

async function refreshReview() {
  const file = commentsFile;
  if (!file) return;
  try {
    const data = await commentsApi(`/api/reviews?file=${encodeURIComponent(file)}`);
    if (file !== commentsFile) return;
    reviewState = data;
    reviewElement('owner').textContent = data.owner ? `送信先: ${data.owner.title || '(無題)'} (${data.owner.status})` : '宛先なし';
    reviewElement('submit').disabled = !data.can_submit;
    const labels = {delivered: '届けた', waiting: '待っています', blocked: '承認待ちで止まっています',
      absent: '不在', unconfigured: '通知は未設定です', failed: '通知に失敗しました', timeout: '通知が時間切れになりました'};
    reviewElement('delivery').textContent = labels[data.delivery.status] || '';
    reviewElement('fallback').hidden = !data.fallback;
    reviewElement('message').value = data.fallback || '';
    const summary = data.reply.summary || '';
    reviewElement('summary').textContent = summary;
    const doc = articleFrame.contentDocument;
    let banner = doc.getElementById('knowledge-review-summary');
    if (!banner) { banner = doc.createElement('p'); banner.id = 'knowledge-review-summary'; doc.body.prepend(banner); }
    banner.textContent = summary;
    const selected = reviewElement('revert').value;
    reviewElement('revert').replaceChildren(...data.rounds.map(round => new Option(`ラウンド ${round.round}`, round.round)));
    if (data.rounds.some(round => String(round.round) === selected)) reviewElement('revert').value = selected;
    reviewElement('diff-toggle').disabled = !data.rounds.length;
    const comments = await commentsApi(`/api/comments?file=${encodeURIComponent(file)}`);
    if (file === commentsFile) showComments(comments.comments);
  } catch (error) { if (file === commentsFile) commentError.textContent = error.message; }
}
reviewElement('verdict').onchange = () => { reviewElement('revert-label').hidden = reviewElement('verdict').value !== 'revert'; };
reviewElement('submit').onclick = async () => {
  const file = commentsFile;
  reviewElement('submit').disabled = true;
  try {
    const verdict = reviewElement('verdict').value;
    const submission = {file, verdict};
    if (verdict === 'revert') submission.revertTo = Number(reviewElement('revert').value);
    await commentsApi('/api/reviews/submit', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(submission)});
  } catch (error) { commentError.textContent = error.message; }
  if (file === commentsFile) await refreshReview();
};
reviewElement('copy').onclick = async () => {
  try { await navigator.clipboard.writeText(reviewElement('message').value); }
  catch (_) { reviewElement('message').select(); }
};
reviewElement('diff-toggle').onclick = async () => {
  const diff = reviewElement('diff');
  if (!diff.hidden) { diff.hidden = true; articleFrame.hidden = false; return; }
  const file = commentsFile;
  const round = reviewState?.rounds.at(-1)?.round;
  if (!round) return;
  try {
    const data = await commentsApi(`/api/reviews/diff?file=${encodeURIComponent(file)}&round=${round}`);
    if (file !== commentsFile) return;
    diff.replaceChildren(...data.changes.map(change => {
      const element = document.createElement(({insert: 'ins', delete: 'del'})[change.kind] || 'span');
      element.textContent = change.text;
      return element;
    }));
    diff.hidden = false;
    articleFrame.hidden = true;
  } catch (error) { commentError.textContent = error.message; }
};
setInterval(() => { if (commentsFile && commentForm.hidden && !commentList.querySelector('details[open]')) refreshReview(); }, 3000);
