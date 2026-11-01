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
let reviewRequest = 0;
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

let displayedSha = "";
let currentComments = [];

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function button(text, action, className) {
  const element = node('button', text, className);
  element.type = 'button';
  element.onclick = action;
  return element;
}
function timestamp(value) {
  const time = node('time', new Date(value).toLocaleString([], {month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit'}));
  time.dateTime = value;
  time.title = new Date(value).toLocaleString();
  return time;
}

function showComments(items) {
  currentComments = items;
  const roots = items.filter(item => !item.reply_to || !items.some(parent => parent.id === item.reply_to));
  const keep = new Set(roots.map(item => item.id));
  for (const card of [...commentList.children]) if (!keep.has(card.dataset.id)) card.remove();
  if (!roots.length) {
    if (!commentList.firstChild) commentList.append(node('p', 'コメントはありません。記事の文章を選択すると追加できます。', 'empty'));
    updateReviewControls();
    return;
  }
  for (const child of [...commentList.children]) if (!child.dataset.id) child.remove();
  for (const item of roots) {
    const replies = items.filter(answer => answer.reply_to === item.id);
    const reply = reviewState?.reply?.orders?.find(order => order.comment_id === item.id);
    const round = reviewState?.round || 0;
    const submitted = new Set(reviewState?.rounds.flatMap(r => r.comment_ids) || []);
    const signature = JSON.stringify([item, replies, reply, round, replies.map(r => submitted.has(r.id))]);
    let card = [...commentList.children].find(element => element.dataset.id === item.id);
    if (card?.dataset.signature === signature) continue;
    // Only changed threads are replaced. Preserve composition, disclosure and focus.
    const oldInput = card?.querySelector('textarea');
    const draft = oldInput?.value;
    const oldDetails = card?.querySelector('details');
    const expanded = oldDetails?.open;
    const focused = card?.contains(document.activeElement) ? document.activeElement.dataset.control : null;
    const selection = oldInput && document.activeElement === oldInput ? [oldInput.selectionStart, oldInput.selectionEnd] : null;
    const replacement = node('article');
    replacement.dataset.id = item.id;
    replacement.dataset.signature = signature;
    const heading = button(`${item.heading || '記事冒頭'} · 本文で見る`, () => {
      const doc = articleFrame.contentDocument;
      const target = item.anchor ? doc.getElementById(item.anchor.replace(/^#/, '')) : doc.body;
      if (target) target.scrollIntoView({behavior: 'smooth'});
    });
    heading.dataset.control = 'heading';
    replacement.append(heading, node('blockquote', item.quote), node('p', `あなた：${item.body}`), timestamp(item.created));
    if (reply) {
      const labels = {done: '対応済み', partial: '一部対応', skipped: '未対応'};
      replacement.append(node('p', `agent：${labels[reply.status]}${reply.reason ? ' — ' + reply.reason : ''}`, 'agent-response'));
    }
    if (reply?.needsConfirm) {
      const details = node('details');
      const answers = replies.filter(answer => answer.created > reviewState.rounds.at(-1).at);
      details.open = expanded ?? !answers.length;
      const marker = node('summary', answers.length ? '確認したいこと · 回答保存済み' : '要回答 · 確認したいこと');
      marker.dataset.control = 'question';
      details.append(marker, node('p', reply.question));
      const answer = node('textarea');
      answer.rows = 3;
      answer.dataset.control = 'answer';
      answer.setAttribute('aria-label', `${item.heading || '記事冒頭'}：${reply.question}への答え`);
      const key = `knowledge-answer:${commentsFile}:${round}:${item.id}`;
      try { answer.value = draft ?? localStorage.getItem(key) ?? ''; } catch (_) { answer.value = draft ?? ''; }
      answer.oninput = () => { try { localStorage.setItem(key, answer.value); } catch (_) {} };
      const save = button('答えを保存', async () => {
        const file = commentsFile;
        save.disabled = true;
        try {
          await commentsApi('/api/comments', {method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({file, quote: item.quote || '(記事全体)', heading: item.heading, anchor: item.anchor,
              body: answer.value, reply_to: item.id})});
          try { localStorage.removeItem(key); } catch (_) {}
          answer.value = '';
          if (file === commentsFile) { await refreshReview(); }
        } catch (error) { commentError.textContent = error.message; }
        finally { save.disabled = false; }
      });
      save.dataset.control = 'save';
      details.append(answer, save);
      replacement.append(details);
    }
    for (const answer of replies) {
      const saved = node('div', undefined, 'saved-answer');
      saved.append(node('p', `あなたの答え：${answer.body}`), timestamp(answer.created),
        node('small', submitted.has(answer.id) ? '送信済み' : '保存済み・次の送信で agent に届きます'));
      replacement.append(saved);
    }
    const actions = node('div', undefined, 'thread-actions');
    const status = button(item.status === 'open' ? '解決にする' : '再開する', () => changeComment(item.id, item.status === 'open' ? 'resolved' : 'open'));
    status.dataset.control = 'status';
    const remove = button('削除', () => {
      if (window.confirm('このコメントを削除しますか？')) changeComment(item.id);
    }, 'danger');
    remove.dataset.control = 'remove';
    actions.append(node('small', `コメント：${item.status === 'open' ? '未解決' : '解決済み'}`), status, remove);
    replacement.append(actions);
    if (card) card.replaceWith(replacement); else commentList.append(replacement);
    if (focused) {
      const target = replacement.querySelector(`[data-control="${focused}"]`);
      target?.focus({preventScroll: true});
      if (selection && target?.tagName === 'TEXTAREA') target.setSelectionRange(...selection);
    }
  }
  updateReviewControls();
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
  ++reviewRequest;
  displayedSha = "";
  currentComments = [];
  reviewElement("verdict").value = "revise";
  reviewElement("revert").value = "";
  reviewElement("diff-toggle").setAttribute("aria-pressed", "false");
  reviewElement("body-toggle").setAttribute("aria-pressed", "true");
  reviewElement("diff-caption").hidden = true;
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
  displayedSha = articleFrame.contentDocument.querySelector('meta[name="knowledge-article-sha256"]')?.content || "";
  commentsPanel.hidden = false;
  updateReviewControls();
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

function updateReviewControls() {
  const data = reviewState;
  const stale = !displayedSha || (data && data.article_sha256 !== displayedSha);
  reviewElement('updated').hidden = !data || !stale;
  const verdict = reviewElement('verdict').value;
  reviewElement('revert-label').hidden = verdict !== 'revert';
  reviewElement('stock').hidden = verdict !== 'approve';
  const labels = {revise: '修正依頼を送信', approve: 'レビュー承認を送信', revert: '過去の版に戻す依頼を送信'};
  const submit = reviewElement('submit');
  submit.textContent = labels[verdict];
  submit.disabled = !data?.can_submit || stale || (verdict === 'revert' && !reviewElement('revert').value);
  submit.hidden = !!data && !data.can_submit;
  reviewElement('waiting').textContent = !data ? '状態を確認しています' : data.can_submit ? '' :
    data.phase === 'complete' ? 'レビュー完了' : data.phase === 'blocked' ? 'レビューは保存済みです。通知先を確認してください。' : `ラウンド ${data.round} を送信済み。agent の返事を待っています。`;
  const open = currentComments.filter(c => c.status === 'open');
  reviewElement('count').textContent = `指摘 ${open.filter(c => !c.reply_to).length} 件・回答 ${open.filter(c => c.reply_to).length} 件を送信`;
}

async function refreshReview() {
  const request = ++reviewRequest;
  const file = commentsFile;
  if (!file) return;
  try {
    const data = await commentsApi(`/api/reviews?file=${encodeURIComponent(file)}`);
    if (file !== commentsFile || request !== reviewRequest) return;
    reviewState = data;
    const phases = {reviewing: 'あなたの確認待ち', waiting_reply: 'agent の返事待ち', complete: 'レビュー完了', blocked: '進行が止まっています'};
    reviewElement('phase').textContent = phases[data.phase];
    reviewElement('round').textContent = `ラウンド ${data.round}`;
    reviewElement('questions').textContent = `要回答 ${data.open_questions} 件`;
    reviewElement('owner').textContent = data.owner ? `宛先：${data.owner.title || '(無題)'}` : '宛先なし';
    const labels = {delivered: '通知済み', waiting: '通知中', blocked: '通知先が停止中',
      absent: '通知先が不在', unconfigured: '通知は未設定', failed: '通知に失敗', timeout: '通知が時間切れ'};
    reviewElement('delivery').textContent = [labels[data.delivery.status], data.owner?.status ? `agent：${data.owner.status}` : ''].filter(Boolean).join(' · ');
    reviewElement('fallback').hidden = !data.fallback;
    reviewElement('message').value = data.fallback || '';
    reviewElement('summary').textContent = data.reply.summary || 'まだ agent からの返事はありません。';
    const selected = reviewElement('revert').value;
    const options = ['', ...data.rounds.map(round => String(round.round))];
    if (JSON.stringify(options) !== JSON.stringify([...reviewElement('revert').options].map(o => o.value))) {
      reviewElement('revert').replaceChildren(new Option('戻す先を選んでください', ''), ...data.rounds.map(round => new Option(`ラウンド ${round.round} の送信時点`, round.round)));
      if (options.includes(selected)) reviewElement('revert').value = selected;
    }
    reviewElement('diff-toggle').disabled = !data.rounds.length;
    updateReviewControls();
    await refreshComments();
  } catch (error) { if (file === commentsFile) commentError.textContent = error.message; }
}
reviewElement('verdict').onchange = updateReviewControls;
reviewElement('revert').onchange = updateReviewControls;
reviewElement('reload').onclick = () => articleFrame.contentWindow.location.reload();
reviewElement('submit').onclick = async () => {
  const file = commentsFile;
  const verdict = reviewElement('verdict').value;
  const submission = {file, verdict, article_sha256: displayedSha};
  if (verdict === 'revert') {
    submission.revertTo = Number(reviewElement('revert').value);
    if (!submission.revertTo) return;
    const dialog = reviewElement('confirm');
    reviewElement('confirm-text').textContent = `ラウンド ${submission.revertTo} の送信時点の本文へ戻すよう agent に頼みます。送信しますか？`;
    dialog.returnValue = '';
    dialog.showModal();
    const confirmed = await new Promise(resolve => dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm'), {once: true}));
    if (!confirmed || file !== commentsFile) return;
  }
  reviewElement('submit').disabled = true;
  commentError.textContent = '';
  try {
    await commentsApi('/api/reviews/submit', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(submission)});
    if (file === commentsFile) reviewElement('verdict').value = 'revise';
  } catch (error) { if (file === commentsFile) commentError.textContent = error.message; }
  if (file === commentsFile) await refreshReview();
};
reviewElement('copy').onclick = async () => {
  try { await navigator.clipboard.writeText(reviewElement('message').value); }
  catch (_) { reviewElement('message').select(); }
};
reviewElement('body-toggle').onclick = () => {
  reviewElement('diff').hidden = true;
  reviewElement('diff-caption').hidden = true;
  articleFrame.hidden = false;
  reviewElement('diff-toggle').setAttribute('aria-pressed', 'false');
  reviewElement('body-toggle').setAttribute('aria-pressed', 'true');
};
reviewElement('diff-toggle').onclick = async () => {
  const file = commentsFile;
  const round = reviewState?.round;
  if (!round) return;
  try {
    const data = await commentsApi(`/api/reviews/diff?file=${encodeURIComponent(file)}&round=${round}`);
    if (file !== commentsFile) return;
    reviewElement('diff').replaceChildren(...data.changes.map(change => {
      const element = node(({insert: 'ins', delete: 'del'})[change.kind] || 'span', change.text);
      return element;
    }));
    reviewElement('comparison').textContent = `ラウンド ${round} の送信時点 → 現在`;
    reviewElement('diff-caption').hidden = false;
    reviewElement('diff').hidden = false;
    articleFrame.hidden = true;
    reviewElement('diff-toggle').setAttribute('aria-pressed', 'true');
    reviewElement('body-toggle').setAttribute('aria-pressed', 'false');
  } catch (error) { commentError.textContent = error.message; }
};
setInterval(() => { if (commentsFile) refreshReview(); }, 3000);
