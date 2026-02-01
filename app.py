import streamlit as st
import random
import base64
from translations import t
from db import (
    init_db, get_or_create_google_user, record_answer, get_question_stats,
    create_session, update_session_score, get_user_sessions,
    get_session_wrong_answers,
    get_user_profile, update_user_profile,
    toggle_favorite, get_favorite_tests,
    get_all_tests, get_test, get_test_questions, get_test_questions_by_ids,
    get_test_tags, rename_test_tag, delete_test_tag, create_test, update_test, delete_test,
    add_question, update_question, delete_question, get_next_question_num,
    get_test_materials, add_test_material, update_test_material, delete_test_material, update_material_transcript,
    get_question_material_links, get_question_material_links_bulk, set_question_material_links,
    create_program, update_program, delete_program, get_program,
    get_all_programs, add_test_to_program, remove_test_from_program,
    get_program_tests, get_program_questions, get_program_tags,
    add_collaborator, remove_collaborator, update_collaborator_role,
    get_collaborators, get_user_role_for_test, get_shared_tests,
    resolve_collaborator_user_id,
    add_program_collaborator, remove_program_collaborator, update_program_collaborator_role,
    get_program_collaborators, get_user_role_for_program, get_shared_programs,
)

init_db()


def _is_logged_in():
    """Return True if user is authenticated."""
    return bool(st.session_state.get("user_id"))


def _try_login():
    """Attempt to log in the user silently, supporting both st.user and st.experimental_user."""
    if st.session_state.get("user_id"):
        return

    user_info = getattr(st, "user", getattr(st, "experimental_user", None))

    if user_info and hasattr(user_info, "is_logged_in"):
        try:
            if user_info.is_logged_in:
                email = user_info.email
                name = getattr(user_info, "name", email) or email

                user_id = get_or_create_google_user(email, name)
                resolve_collaborator_user_id(email, user_id)
                st.session_state.user_id = user_id
                st.session_state.username = name
        except Exception as e:
            st.warning(t("auth_not_configured", e=e))


def _difficulty_score(q, question_stats):
    """Return a score that prioritizes questions the user gets wrong more often."""
    stats = question_stats.get(q["id"])
    if stats is None:
        return 0.5
    total = stats["correct"] + stats["wrong"]
    if total == 0:
        return 0.5
    return stats["wrong"] / total


def select_balanced_questions(questions, selected_tags, num_questions, question_stats=None):
    """Select questions balanced across selected tags, prioritizing difficult ones."""
    filtered = [q for q in questions if q["tag"] in selected_tags]

    if not filtered:
        return []

    if num_questions >= len(filtered):
        random.shuffle(filtered)
        return filtered

    questions_by_tag = {}
    for q in filtered:
        tag = q["tag"]
        if tag not in questions_by_tag:
            questions_by_tag[tag] = []
        questions_by_tag[tag].append(q)

    for tag in questions_by_tag:
        if question_stats:
            questions_by_tag[tag].sort(
                key=lambda q: _difficulty_score(q, question_stats),
                reverse=True,
            )
        else:
            random.shuffle(questions_by_tag[tag])

    selected = []
    tag_list = list(questions_by_tag.keys())
    tag_index = 0

    while len(selected) < num_questions:
        tag = tag_list[tag_index % len(tag_list)]
        if questions_by_tag[tag]:
            selected.append(questions_by_tag[tag].pop(0))
        else:
            tag_list.remove(tag)
            if not tag_list:
                break
        tag_index += 1

    random.shuffle(selected)
    return selected


def shuffle_question_options(questions):
    """Shuffle options for each question, updating answer_index accordingly."""
    for q in questions:
        correct_option = q["options"][q["answer_index"]]
        shuffled = list(q["options"])
        random.shuffle(shuffled)
        q["options"] = shuffled
        q["answer_index"] = shuffled.index(correct_option)
    return questions


def reset_quiz():
    """Reset quiz state."""
    for key in ["quiz_started", "questions", "current_index", "answered",
                "score", "show_result", "selected_answer", "wrong_questions",
                "round_history", "current_round", "current_test_id",
                "current_session_id", "session_score_saved", "active_quiz_level"]:
        if key in st.session_state:
            del st.session_state[key]


LANGUAGE_OPTIONS = ["", "es", "en", "fr", "ca", "de", "pt", "it"]
LANGUAGE_KEYS = {"": "", "es": "lang_es", "en": "lang_en", "fr": "lang_fr", "ca": "lang_ca", "de": "lang_de", "pt": "lang_pt", "it": "lang_it"}
LANGUAGE_FLAGS = {"es": "🇪🇸", "en": "🇬🇧", "fr": "🇫🇷", "ca": "🇦🇩", "de": "🇩🇪", "pt": "🇵🇹", "it": "🇮🇹"}
UI_LANGUAGES = ["es", "en", "fr", "ca"]
UI_LANG_LABELS = {"es": "🇪🇸 ES", "en": "🇬🇧 EN", "fr": "🇫🇷 FR", "ca": "🇦🇩 CA"}


def _fetch_youtube_transcript(url):
    """Fetch transcript for a YouTube video. Returns formatted text or empty string."""
    import re
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return ""
    # Extract video ID
    m = re.search(r'(?:v=|youtu\.be/|/embed/|/v/)([a-zA-Z0-9_-]{11})', url)
    if not m:
        return ""
    video_id = m.group(1)
    try:
        api = YouTubeTranscriptApi()
        lang = st.session_state.get("lang", "es")
        try:
            result = api.fetch(video_id, languages=[lang, "en", "es", "fr", "ca"])
        except Exception:
            result = api.fetch(video_id)
        lines = []
        for snippet in result.snippets:
            mins = int(snippet.start) // 60
            secs = int(snippet.start) % 60
            lines.append(f"[{mins}:{secs:02d}] {snippet.text}")
        return "\n".join(lines)
    except Exception:
        return ""


@st.dialog(t("transcript"), width="large")
def _show_transcript_dialog(transcript_text):
    st.text_area(t("transcript"), value=transcript_text, height=400, disabled=True)


def _generate_topics_from_transcript(transcript_text, existing_tags=None):
    """Use Google Gemini to generate topic suggestions from a transcript."""
    import os
    try:
        import google.generativeai as genai
    except ImportError:
        return []
    api_key = os.environ.get("GOOGLE_API_KEY") or st.secrets.get("GOOGLE_API_KEY", "")
    if not api_key:
        return []
    genai.configure(api_key=api_key)
    lang = st.session_state.get("lang", "es")
    lang_names = {"es": "Spanish", "en": "English", "fr": "French", "ca": "Catalan"}
    lang_name = lang_names.get(lang, "Spanish")
    existing_str = ", ".join(existing_tags) if existing_tags else "none"
    prompt = (
        f"Analyze this video transcript and extract the main topics/themes covered. "
        f"Return ONLY a list of short topic names (2-4 words each), one per line, no numbering, no bullets. "
        f"Topics should be in {lang_name}. "
        f"Existing topics already in the test: {existing_str}. "
        f"Do not repeat existing topics. Suggest 5-15 new topics.\n\n"
        f"Transcript:\n{transcript_text[:8000]}"
    )
    try:
        model = genai.GenerativeModel("gemini-2.0-flash")
        response = model.generate_content(prompt)
        lines = [line.strip() for line in response.text.strip().split("\n") if line.strip()]
        return lines
    except Exception as e:
        st.error(f"{t('transcript_error')} {e}")
        return []


@st.dialog(t("generate_topics_title"), width="large")
def _show_generate_topics_dialog(test_id, transcript_text, existing_tags):
    if "generated_topics" not in st.session_state:
        with st.spinner(t("generating_topics")):
            suggestions = _generate_topics_from_transcript(transcript_text, existing_tags)
        st.session_state.generated_topics = suggestions

    topics = st.session_state.generated_topics
    st.write(t("generated_topics_instructions"))

    edited_text = st.text_area(
        t("topics"),
        value="\n".join(topics),
        height=300,
        key="gen_topics_editor",
    )

    existing_set = {t_name.strip().lower() for t_name in existing_tags}
    new_topics = [line.strip() for line in edited_text.split("\n") if line.strip()]
    dupes = [tp for tp in new_topics if tp.strip().lower() in existing_set]
    if dupes:
        st.warning(t("duplicate_topics_warning", topics=", ".join(dupes)))

    col_confirm, col_cancel = st.columns(2)
    with col_confirm:
        if st.button(t("confirm"), type="primary", key="confirm_gen_topics"):
            added = 0
            for topic_name in new_topics:
                if topic_name.strip().lower() not in existing_set:
                    next_num = get_next_question_num(test_id)
                    add_question(
                        test_id, next_num, topic_name.strip(),
                        t("new_question_text"),
                        [t("option_a"), t("option_b"), t("option_c"), t("option_d")],
                        0, "",
                    )
                    existing_set.add(topic_name.strip().lower())
                    added += 1
            del st.session_state["generated_topics"]
            st.success(t("topics_added", n=added))
            st.rerun()
    with col_cancel:
        if st.button(t("cancel"), key="cancel_gen_topics"):
            del st.session_state["generated_topics"]
            st.rerun()


def _parse_pause_times(text):
    """Parse pause times like '1:30(2), 3:00, 5:45(3)' to JSON string of [{t, n}]."""
    import json as _j, re as _re
    if not text or not text.strip():
        return ""
    stops = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        # Extract optional (N) suffix for question count
        m = _re.match(r'^([\d:]+)\s*(?:\((\d+)\))?$', part)
        if not m:
            continue
        time_str, n_str = m.group(1), m.group(2)
        n = int(n_str) if n_str else 1
        pieces = time_str.split(":")
        try:
            if len(pieces) == 2:
                secs = int(pieces[0]) * 60 + int(pieces[1])
            else:
                secs = int(pieces[0])
        except ValueError:
            continue
        stops.append({"t": secs, "n": n})
    stops.sort(key=lambda x: x["t"])
    return _j.dumps(stops) if stops else ""


def _format_pause_times(pause_json):
    """Convert JSON pause_times back to display string like '1:30(2), 3:00'."""
    import json as _j
    if not pause_json:
        return ""
    try:
        stops = _j.loads(pause_json)
    except (ValueError, TypeError):
        return pause_json  # return as-is if not valid JSON
    # Handle old format (plain list of seconds)
    if stops and isinstance(stops[0], (int, float)):
        stops = [{"t": s, "n": 1} for s in stops]
    parts = []
    for s in stops:
        mins = int(s["t"]) // 60
        secs = int(s["t"]) % 60
        time_str = f"{mins}:{secs:02d}"
        n = s.get("n", 1)
        if n > 1:
            time_str += f"({n})"
        parts.append(time_str)
    return ", ".join(parts)


def _lang_display(code):
    """Return display label for a language code."""
    if not code:
        return ""
    flag = LANGUAGE_FLAGS.get(code, "")
    name = t(LANGUAGE_KEYS.get(code, ""), ) if code in LANGUAGE_KEYS else code
    return f"{flag} {name}".strip()


def _time_to_secs(time_str):
    """Convert a time string like '1:30' or '1:00-2:00' (uses start) to seconds."""
    time_str = time_str.strip().split("-")[0].strip()  # take start of range
    pieces = time_str.split(":")
    try:
        return int(pieces[0]) * 60 + int(pieces[1]) if len(pieces) == 2 else int(pieces[0])
    except (ValueError, IndexError):
        return 0


def _render_material_refs(question_db_id, test_id):
    """Show clickable material references for a question after explanation."""
    links = get_question_material_links(question_db_id)
    if not links:
        return
    materials = get_test_materials(test_id)
    mat_by_id = {m["id"]: m for m in materials}
    type_icons = {"pdf": "📄", "youtube": "▶️", "image": "🖼️", "url": "🔗"}
    refs = []
    for lk in links:
        mat = mat_by_id.get(lk["material_id"])
        if not mat:
            continue
        icon = type_icons.get(mat["material_type"], "📎")
        title = mat["title"] or mat["url"] or t("no_title")
        ctx = lk.get("context", "")
        if mat["material_type"] == "youtube" and mat.get("url"):
            if ctx:
                label = f"{icon} {title} ({ctx})"
                # Link to first timestamp (handles ranges like 1:00-2:00)
                first_time = ctx.split(",")[0].strip()
                secs = _time_to_secs(first_time)
                refs.append(f'<a href="{mat["url"]}&t={secs}" target="_blank" style="text-decoration:none;">{label}</a>')
            else:
                refs.append(f'<a href="{mat["url"]}" target="_blank" style="text-decoration:none;">{icon} {title}</a>')
        elif mat["material_type"] == "url" and mat.get("url"):
            refs.append(f'<a href="{mat["url"]}" target="_blank" style="text-decoration:none;">{icon} {title}</a>')
        elif mat["material_type"] == "pdf" and ctx:
            refs.append(f"{icon} {title} (p. {ctx})")
        else:
            refs.append(f"{icon} {title}")
    if refs:
        st.caption(t("material_references") + ": " + " · ".join(refs), unsafe_allow_html=True)


@st.dialog("📎", width="large")
def _show_material_dialog(mat, label):
    """Show material content in a dialog."""
    st.subheader(label)
    if mat["material_type"] == "youtube" and mat["url"]:
        st.video(mat["url"])
    elif mat["material_type"] == "url" and mat["url"]:
        st.markdown(f"🔗 **[{mat['url']}]({mat['url']})**")
        import streamlit.components.v1 as components
        components.iframe(mat["url"], height=600, scrolling=True)
    elif mat["material_type"] == "image" and mat["file_data"]:
        st.image(mat["file_data"])
    elif mat["material_type"] == "pdf" and mat["file_data"]:
        import base64
        import streamlit.components.v1 as components
        b64 = base64.b64encode(mat["file_data"]).decode()
        pdf_html = f"""
<!DOCTYPE html>
<html>
<head>
<script src="https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js"></script>
<style>
body {{ margin: 0; background: #525659; overflow-y: auto; }}
canvas {{ display: block; margin: 4px auto; }}
</style>
</head>
<body>
<script>
pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';
var pdfData = atob("{b64}");
var uint8 = new Uint8Array(pdfData.length);
for (var i = 0; i < pdfData.length; i++) uint8[i] = pdfData.charCodeAt(i);
pdfjsLib.getDocument({{data: uint8}}).promise.then(function(pdf) {{
  for (var p = 1; p <= pdf.numPages; p++) {{
    (function(pageNum) {{
      pdf.getPage(pageNum).then(function(page) {{
        var scale = 1.5;
        var viewport = page.getViewport({{scale: scale}});
        var canvas = document.createElement('canvas');
        canvas.width = viewport.width;
        canvas.height = viewport.height;
        document.body.appendChild(canvas);
        page.render({{canvasContext: canvas.getContext('2d'), viewport: viewport}});
      }});
    }})(p);
  }}
}});
</script>
</body>
</html>
"""
        components.html(pdf_html, height=620, scrolling=True)
        st.download_button(
            t("download"),
            data=mat["file_data"],
            file_name=f"{label}.pdf",
            key="dialog_dl_pdf",
        )
    else:
        st.write(t("no_preview"))
    if st.button(t("close"), key="close_mat_dialog", use_container_width=True):
        # Clear all show_mat flags
        for k in list(st.session_state.keys()):
            if k.startswith("show_mat_"):
                del st.session_state[k]
        st.rerun()


@st.dialog("📌", width="large")
def _show_study_dialog(mat, label, questions):
    """Play a YouTube video, auto-pause after 60s, show question, resume on answer."""
    import re
    import json as _json
    import streamlit.components.v1 as components

    url = mat.get("url", "")
    match = re.search(r"(?:v=|youtu\.be/|embed/)([\w-]{11})", url)
    video_id = match.group(1) if match else ""

    if not video_id:
        st.video(url)
        return

    # Prepare questions JSON for JS (strip file data, include material refs)
    test_id = st.session_state.get("selected_test")
    all_materials = get_test_materials(test_id) if test_id else []
    mat_by_id = {m["id"]: m for m in all_materials}
    q_db_ids = [q["db_id"] for q in questions]
    all_links = get_question_material_links_bulk(q_db_ids) if q_db_ids else {}

    q_data = []
    for q in questions:
        refs = []
        for lk in all_links.get(q["db_id"], []):
            m = mat_by_id.get(lk["material_id"])
            if not m:
                continue
            type_icons = {"pdf": "📄", "youtube": "▶️", "image": "🖼️", "url": "🔗"}
            icon = type_icons.get(m["material_type"], "📎")
            title = m["title"] or m["url"] or ""
            ctx = lk.get("context", "")
            url = m.get("url", "")
            if m["material_type"] == "youtube" and url and ctx:
                first = ctx.split(",")[0].strip()
                secs = _time_to_secs(first)
                refs.append({"icon": icon, "title": title, "ctx": ctx, "url": f"{url}&t={secs}"})
            elif m["material_type"] in ("youtube", "url") and url:
                refs.append({"icon": icon, "title": title, "ctx": ctx, "url": url})
            else:
                suffix = f" (p. {ctx})" if ctx else ""
                refs.append({"icon": icon, "title": title + suffix, "ctx": "", "url": ""})
        q_data.append({
            "question": q["question"], "options": q["options"],
            "answer_index": q["answer_index"], "explanation": q.get("explanation", ""),
            "tag": q.get("tag", ""), "refs": refs,
        })
    q_json = _json.dumps(q_data, ensure_ascii=False)

    lang = st.session_state.get("lang", "es")
    correct_label = t("correct")
    incorrect_label = t("incorrect")
    continue_label = t("study_continue")

    # Parse configured pause times or fall back to repeating 60s
    pause_times_raw = mat.get("pause_times", "")
    if pause_times_raw:
        try:
            stops_list = _json.loads(pause_times_raw)
        except (ValueError, TypeError):
            stops_list = []
        # Handle old format (plain list of seconds) -> convert to {t, n} objects
        if stops_list and isinstance(stops_list[0], (int, float)):
            stops_list = [{"t": s, "n": 1} for s in stops_list]
    else:
        stops_list = []
    stops_json = _json.dumps(stops_list) if stops_list else "[]"

    study_html = f"""
<!DOCTYPE html>
<html><head>
<style>
body {{ margin:0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background:#0e1117; color:#fafafa; }}
#player-wrap {{ position:relative; }}
#quiz-overlay {{
  display:none; position:absolute; top:0; left:0; width:100%; height:100%;
  background:rgba(14,17,23,0.95); z-index:10;
  display:none; flex-direction:column; justify-content:center; padding:24px; box-sizing:border-box;
  overflow-y:auto;
}}
#quiz-overlay.active {{ display:flex; }}
#quiz-overlay h3 {{ margin:0 0 6px; font-size:1.1em; }}
#quiz-overlay .tag {{ color:#888; font-size:0.85em; margin-bottom:16px; }}
.opt-btn {{
  display:block; width:100%; padding:10px 16px; margin:4px 0; border:1px solid #444;
  border-radius:8px; background:#1a1d24; color:#fafafa; font-size:0.95em;
  cursor:pointer; text-align:left;
}}
.opt-btn:hover {{ background:#262a33; }}
.opt-btn.correct {{ background:#1b5e20; border-color:#4caf50; cursor:default; }}
.opt-btn.wrong {{ background:#b71c1c; border-color:#f44336; cursor:default; }}
.opt-btn.dimmed {{ opacity:0.5; cursor:default; }}
.result {{ margin:12px 0; padding:8px 12px; border-radius:6px; font-weight:bold; }}
.result.ok {{ background:#1b5e20; }}
.result.fail {{ background:#b71c1c; }}
.explanation {{ margin:8px 0; padding:10px 12px; background:#1a2733; border-radius:6px; font-size:0.9em; }}
#refs-area {{ margin:8px 0; padding:10px 12px; background:#1a2733; border-radius:6px; font-size:0.85em; }}
#countdown {{ position:absolute; top:8px; right:12px; background:rgba(0,0,0,0.7); color:#fff;
  padding:4px 10px; border-radius:12px; font-size:0.85em; z-index:5; }}
.continue-btn {{
  display:block; width:100%; padding:12px 16px; margin:16px 0 0; border:none;
  border-radius:8px; background:#1b6ef3; color:#fff; font-size:1em; font-weight:bold;
  cursor:pointer; text-align:center;
}}
.continue-btn:hover {{ background:#1558c9; }}
</style>
</head><body>
<div id="player-wrap">
  <div id="player"></div>
  <div id="countdown"></div>
  <div id="quiz-overlay"></div>
</div>
<script>
var QUESTIONS = {q_json};
var CORRECT_LABEL = {_json.dumps(correct_label)};
var INCORRECT_LABEL = {_json.dumps(incorrect_label)};
var CONTINUE_LABEL = {_json.dumps(continue_label)};
var CONFIGURED_STOPS = {stops_json};
var FALLBACK_INTERVAL = 60;

var tag = document.createElement('script');
tag.src = "https://www.youtube.com/iframe_api";
document.head.appendChild(tag);

var player, timer = null, questionShown = false;
var stopIdx = 0;
var questionsRemaining = 0;
// If configured stops exist, use them; otherwise use repeating fallback
var useConfigured = CONFIGURED_STOPS.length > 0;
var nextStop = useConfigured ? CONFIGURED_STOPS[0].t : FALLBACK_INTERVAL;
var overlay = document.getElementById('quiz-overlay');
var countdownEl = document.getElementById('countdown');

function onYouTubeIframeAPIReady() {{
  player = new YT.Player('player', {{
    width: '100%', height: '400',
    videoId: '{video_id}',
    events: {{
      'onReady': function(e) {{ e.target.playVideo(); }},
      'onStateChange': onStateChange
    }}
  }});
}}

function onStateChange(e) {{
  if (e.data == YT.PlayerState.PLAYING) {{
    questionShown = false;
    startTimer();
  }} else {{
    stopTimer();
  }}
}}

function startTimer() {{
  stopTimer();
  if (nextStop < 0) return; // no more stops
  timer = setInterval(function() {{
    var cur = player.getCurrentTime();
    updateCountdown(cur);
    if (cur >= nextStop && !questionShown) {{
      questionShown = true;
      stopTimer();
      player.pauseVideo();
      questionsRemaining = (useConfigured && stopIdx < CONFIGURED_STOPS.length) ? CONFIGURED_STOPS[stopIdx].n : 1;
      showQuestion();
    }}
  }}, 500);
}}

function stopTimer() {{
  if (timer) {{ clearInterval(timer); timer = null; }}
}}

function updateCountdown(cur) {{
  if (nextStop < 0) {{ countdownEl.style.display = 'none'; return; }}
  var remaining = Math.max(0, Math.ceil(nextStop - cur));
  if (remaining > 0 && !questionShown) {{
    var m = Math.floor(remaining / 60);
    var s = remaining % 60;
    countdownEl.textContent = (m > 0 ? m + ':' : '') + (s < 10 ? '0' : '') + s;
    countdownEl.style.display = 'block';
  }} else {{
    countdownEl.style.display = 'none';
  }}
}}

function advanceStop() {{
  if (useConfigured) {{
    stopIdx++;
    if (stopIdx < CONFIGURED_STOPS.length) {{
      nextStop = CONFIGURED_STOPS[stopIdx].t;
    }} else {{
      nextStop = -1; // no more stops
    }}
  }} else {{
    nextStop = player.getCurrentTime() + FALLBACK_INTERVAL;
  }}
}}

function resumeVideo() {{
  overlay.classList.remove('active');
  overlay.innerHTML = '';
  advanceStop();
  questionShown = false;
  player.playVideo();
}}

function showQuestion() {{
  var q = QUESTIONS[Math.floor(Math.random() * QUESTIONS.length)];
  var html = '<h3>' + escHtml(q.question) + '</h3>';
  html += '<div class="tag">' + escHtml(q.tag.replace(/_/g, ' ')) + '</div>';
  for (var i = 0; i < q.options.length; i++) {{
    html += '<button class="opt-btn" data-idx="' + i + '" data-correct="' + q.answer_index + '">'
          + escHtml(q.options[i]) + '</button>';
  }}
  html += '<div id="result-area"></div>';
  if (q.explanation) {{
    html += '<div class="explanation" style="display:none" id="expl-area"><b>Explanation:</b> ' + escHtml(q.explanation) + '</div>';
  }}
  if (q.refs && q.refs.length > 0) {{
    html += '<div style="display:none" id="refs-area"><b>📚 References:</b> ';
    var parts = [];
    for (var r = 0; r < q.refs.length; r++) {{
      var ref = q.refs[r];
      if (ref.url) {{
        parts.push('<a href="' + ref.url + '" target="_blank" style="color:#1a73e8;text-decoration:none;">' + ref.icon + ' ' + escHtml(ref.title) + (ref.ctx ? ' (' + escHtml(ref.ctx) + ')' : '') + '</a>');
      }} else {{
        parts.push(ref.icon + ' ' + escHtml(ref.title));
      }}
    }}
    html += parts.join(' &middot; ') + '</div>';
  }}
  overlay.innerHTML = html;
  overlay.classList.add('active');

  overlay.querySelectorAll('.opt-btn').forEach(function(btn) {{
    btn.addEventListener('click', function() {{
      var idx = parseInt(this.dataset.idx);
      var correct = parseInt(this.dataset.correct);
      var buttons = overlay.querySelectorAll('.opt-btn');
      buttons.forEach(function(b) {{
        var bi = parseInt(b.dataset.idx);
        if (bi === correct) b.classList.add('correct');
        else if (bi === idx && idx !== correct) b.classList.add('wrong');
        else b.classList.add('dimmed');
        b.style.pointerEvents = 'none';
      }});
      var ra = document.getElementById('result-area');
      if (idx === correct) {{
        ra.innerHTML = '<div class="result ok">' + escHtml(CORRECT_LABEL) + '</div>';
      }} else {{
        ra.innerHTML = '<div class="result fail">' + escHtml(INCORRECT_LABEL) + '</div>';
      }}
      var expl = document.getElementById('expl-area');
      if (expl) expl.style.display = 'block';
      var refsEl = document.getElementById('refs-area');
      if (refsEl) refsEl.style.display = 'block';
      questionsRemaining--;
      var continueBtn = document.createElement('button');
      continueBtn.className = 'continue-btn';
      if (questionsRemaining > 0) {{
        continueBtn.textContent = '→';
        continueBtn.addEventListener('click', function() {{
          showQuestion();
        }});
      }} else {{
        continueBtn.textContent = CONTINUE_LABEL;
        continueBtn.addEventListener('click', function() {{
          resumeVideo();
        }});
      }}
      overlay.appendChild(continueBtn);
    }});
  }});
}}

function escHtml(s) {{
  var d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}}
</script>
</body></html>
"""
    st.info(t("study_watch"))
    components.html(study_html, height=460)

    if st.button(t("close"), key="close_study_dialog", use_container_width=True):
        for k in list(st.session_state.keys()):
            if k.startswith("study_"):
                del st.session_state[k]
        st.rerun()


def _render_test_card(test, favorites, prefix="", has_access=True):
    """Render a single test card with heart and select button."""
    test_id = test["id"]
    is_fav = test_id in favorites
    logged_in = _is_logged_in()

    with st.container(border=True):
        if logged_in:
            col_fav, col_info, col_btn = st.columns([0.5, 4, 1])
            with col_fav:
                heart = "❤️" if is_fav else "🤍"
                if st.button(heart, key=f"{prefix}fav_{test_id}"):
                    toggle_favorite(st.session_state.user_id, test_id)
                    st.rerun()
        else:
            col_info, col_btn = st.columns([4, 1])
        with col_info:
            title_display = test["title"]
            if test.get("visibility") == "private" and not has_access:
                title_display = "🔒 " + title_display
            st.subheader(title_display)
            if test.get("description"):
                st.write(test["description"])
            meta = t("n_questions", n=test['question_count'])
            if test.get("author"):
                meta += f"  ·  {t('author', name=test['author'])}"
            if test.get("language"):
                meta += f"  ·  {_lang_display(test['language'])}"
            st.caption(meta)
        with col_btn:
            if has_access:
                if st.button(t("select"), key=f"{prefix}select_{test_id}", use_container_width=True):
                    st.session_state.selected_test = test_id
                    st.session_state.page = "Configurar Test"
                    st.rerun()
            else:
                st.button(t("select"), key=f"{prefix}select_{test_id}", use_container_width=True, disabled=True)


def show_test_catalog():
    """Show a searchable catalog of available tests."""
    user_id = st.session_state.get("user_id")
    all_tests = get_all_tests(user_id)

    if not all_tests:
        st.error(t("no_tests"))
        return

    st.header(t("available_tests"))

    col_search, col_lang = st.columns([3, 1])
    with col_search:
        search = st.text_input(t("search_test"), placeholder=t("search_placeholder"), key="test_search")
    with col_lang:
        # Build language filter options from available tests
        available_langs = sorted({tt["language"] for tt in all_tests if tt.get("language")})
        lang_options = [""] + available_langs
        lang_labels = {code: _lang_display(code) if code else t("all_languages") for code in lang_options}
        selected_lang = st.selectbox(
            t("language"), options=lang_options,
            format_func=lambda x: lang_labels[x],
            key="test_lang_filter",
        )

    logged_in = _is_logged_in()
    favorites = get_favorite_tests(st.session_state.user_id) if logged_in else set()

    if logged_in:
        if st.button(t("create_test"), type="secondary"):
            st.session_state.page = "Crear Test"
            st.rerun()

    filtered_tests = [
        tt for tt in all_tests
        if (not search or search.lower() in tt["title"].lower())
        and (not selected_lang or tt.get("language") == selected_lang)
    ]

    if not filtered_tests:
        st.info(t("no_tests_found"))
        return

    fav_tests = [tt for tt in filtered_tests if tt["id"] in favorites]
    other_tests = [tt for tt in filtered_tests if tt["id"] not in favorites]

    # Shared with me
    shared_tests = []
    shared_test_ids = set()
    if logged_in:
        shared_tests = get_shared_tests(st.session_state.user_id)
        shared_test_ids = {tt["id"] for tt in shared_tests}

    # Build set of test IDs the user has access to (owner or collaborator)
    accessible_ids = shared_test_ids.copy()
    if logged_in:
        accessible_ids |= {tt["id"] for tt in all_tests if tt.get("owner_id") == st.session_state.user_id}

    def _has_access(tt):
        vis = tt.get("visibility", "public")
        if vis == "public":
            return True
        return tt["id"] in accessible_ids

    if fav_tests:
        st.subheader(t("favorites"))
        for test in fav_tests:
            _render_test_card(test, favorites, prefix="fav_", has_access=_has_access(test))

    if shared_tests:
        st.subheader(t("shared_with_me"))
        for test in shared_tests:
            if test["id"] not in {tt["id"] for tt in fav_tests}:
                _render_test_card(test, favorites, prefix="shared_", has_access=True)

    other_tests = [tt for tt in other_tests if tt["id"] not in shared_test_ids]
    if other_tests:
        if fav_tests or shared_tests:
            st.subheader(t("all_tests"))
        for test in other_tests:
            _render_test_card(test, favorites, has_access=_has_access(test))


def show_test_config():
    """Show configuration for the selected test before starting."""
    test_id = st.session_state.get("selected_test")
    if not test_id:
        st.session_state.page = "Tests"
        st.rerun()
        return

    test = get_test(test_id)
    if not test:
        st.error(t("test_not_found"))
        return

    # Access control for private/hidden tests
    visibility = test.get("visibility", "public")
    if visibility != "public":
        logged_in_uid = st.session_state.get("user_id")
        has_test_access = (
            logged_in_uid and (
                test["owner_id"] == logged_in_uid
                or get_user_role_for_test(test_id, logged_in_uid) is not None
            )
        )
        if not has_test_access:
            st.error(t("test_private_no_access"))
            if st.button(t("back_to_tests")):
                del st.session_state.selected_test
                st.session_state.page = "Tests"
                st.rerun()
            return

    questions = get_test_questions(test_id)
    tags = get_test_tags(test_id)

    st.header(test["title"])
    if test.get("description"):
        st.write(test["description"])
    caption_parts = []
    if test.get("author"):
        caption_parts.append(t("author", name=test['author']))
    if test.get("language"):
        caption_parts.append(_lang_display(test['language']))
    if caption_parts:
        st.caption("  ·  ".join(caption_parts))

    # Show materials if any
    materials = get_test_materials(test_id)
    if materials:
        with st.expander(t("reference_materials", n=len(materials))):
            for mat in materials:
                type_icons = {"pdf": "📄", "youtube": "▶️", "image": "🖼️", "url": "🔗"}
                icon = type_icons.get(mat["material_type"], "📎")
                label = mat["title"] or mat["url"] or t("no_title")
                has_download = mat["material_type"] in ("pdf", "image") and mat.get("file_data")
                has_link = mat["material_type"] in ("url", "youtube") and mat.get("url")
                has_extra = has_download or has_link
                num_icons = 2 + (1 if has_extra else 0)
                cols = st.columns([6] + [1] * num_icons)
                with cols[0]:
                    st.write(f"{icon} {label}")
                col_idx = 1
                if has_download:
                    with cols[col_idx]:
                        ext = "pdf" if mat["material_type"] == "pdf" else "png"
                        st.download_button(
                            "⬇️", data=mat["file_data"],
                            file_name=f"{label}.{ext}",
                            key=f"dl_mat_{mat['id']}",
                        )
                    col_idx += 1
                elif has_link:
                    with cols[col_idx]:
                        st.markdown(f'<a href="{mat["url"]}" target="_blank" style="text-decoration:none;font-size:1.4em;">🔗</a>', unsafe_allow_html=True)
                    col_idx += 1
                with cols[col_idx]:
                    if st.button("👁️", key=f"view_mat_{mat['id']}"):
                        for k in list(st.session_state.keys()):
                            if k.startswith("show_mat_"):
                                del st.session_state[k]
                        st.session_state[f"show_mat_{mat['id']}"] = True
                        st.rerun()
                with cols[col_idx + 1]:
                    is_youtube = mat["material_type"] == "youtube" and mat.get("url")
                    can_study = is_youtube and bool(questions)
                    if can_study:
                        if st.button("📌", key=f"study_mat_{mat['id']}"):
                            for k in list(st.session_state.keys()):
                                if k.startswith("study_"):
                                    del st.session_state[k]
                            st.session_state.study_mat_id = mat['id']
                            st.rerun()
                    else:
                        st.button("📌", key=f"study_mat_{mat['id']}", disabled=True)

            # Render dialog for the active material (only one at a time)
            for mat in materials:
                if st.session_state.get(f"show_mat_{mat['id']}"):
                    label = mat["title"] or mat["url"] or t("no_title")
                    _show_material_dialog(mat, label)
                    break

            # Render study dialog if active
            study_mat_id = st.session_state.get("study_mat_id")
            if study_mat_id:
                for mat in materials:
                    if mat["id"] == study_mat_id:
                        label = mat["title"] or mat["url"] or t("no_title")
                        _show_study_dialog(mat, label, questions)
                        break

    is_owner = _is_logged_in() and test["owner_id"] == st.session_state.user_id
    can_edit = is_owner or (
        _is_logged_in() and get_user_role_for_test(test_id, st.session_state.user_id) in ("guest", "reviewer", "admin")
    )
    # Students can take the test but not view the editor
    num_cols = 1 + (2 if can_edit else 0)
    cols_buttons = st.columns(num_cols)
    col_idx = 0
    with cols_buttons[col_idx]:
        if st.button(t("back_to_tests")):
            del st.session_state.selected_test
            st.session_state.page = "Tests"
            st.rerun()
    if can_edit:
        col_idx += 1
        with cols_buttons[col_idx]:
            if st.button(t("edit_test")):
                st.session_state.editing_test_id = test_id
                st.session_state.page = "Editar Test"
                st.rerun()
        col_idx += 1
        with cols_buttons[col_idx]:
            import json as _json
            # Build full export with materials and question-material links
            all_q_db_ids = [q["db_id"] for q in questions]
            all_q_mat_links = get_question_material_links_bulk(all_q_db_ids) if all_q_db_ids else {}
            export_materials = []
            for mat in materials:
                export_mat = {
                    "id": mat["id"],
                    "material_type": mat["material_type"],
                    "title": mat.get("title", ""),
                    "url": mat.get("url", ""),
                    "pause_times": mat.get("pause_times", ""),
                }
                export_materials.append(export_mat)
            export_questions = []
            for q in questions:
                eq = {
                    "id": q["id"],
                    "tag": q["tag"],
                    "question": q["question"],
                    "options": q["options"],
                    "answer_index": q["answer_index"],
                    "explanation": q.get("explanation", ""),
                }
                links = all_q_mat_links.get(q["db_id"], [])
                if links:
                    eq["material_refs"] = [
                        {"material_id": lk["material_id"], "context": lk.get("context", "")}
                        for lk in links
                    ]
                export_questions.append(eq)
            export_collabs = []
            for c in get_collaborators(test_id):
                export_collabs.append({"email": c["email"], "role": c["role"]})
            export_data = {
                "title": test["title"],
                "description": test.get("description", ""),
                "author": test.get("author", ""),
                "language": test.get("language", ""),
                "visibility": test.get("visibility", "public"),
                "materials": export_materials,
                "collaborators": export_collabs,
                "questions": export_questions,
            }
            st.download_button(
                t("export_json"),
                data=_json.dumps(export_data, ensure_ascii=False, indent=2),
                file_name=f"{test['title']}.json",
                mime="application/json",
                key="export_test_json",
            )

    st.subheader(t("configuration"))

    if not questions:
        st.info(t("no_questions"))
        return

    num_questions = st.number_input(
        t("num_questions"),
        min_value=1,
        max_value=len(questions),
        value=min(25, len(questions))
    )

    level_options = ["easy", "difficult"]
    level_labels = {"easy": t("level_easy"), "difficult": t("level_difficult")}
    quiz_level = st.selectbox(
        t("level"),
        options=level_options,
        format_func=lambda x: level_labels[x],
        key="quiz_level",
    )

    st.write(t("topics_to_include"))
    selected_tags = []
    cols = st.columns(2)
    for i, tag in enumerate(tags):
        tag_display = tag.replace("_", " ").title()
        if cols[i % 2].checkbox(tag_display, value=True, key=f"tag_{tag}"):
            selected_tags.append(tag)

    if not selected_tags:
        st.warning(t("select_at_least_one_topic"))
    else:
        filtered_count = len([q for q in questions if q["tag"] in selected_tags])
        st.info(t("available_questions_with_topics", n=filtered_count))

        if st.button(t("start_test"), type="primary"):
            logged_in = _is_logged_in()
            stats = get_question_stats(st.session_state.user_id, test_id) if logged_in else None
            quiz_questions = select_balanced_questions(
                questions, selected_tags, num_questions, stats
            )
            session_id = None
            if logged_in:
                session_id = create_session(
                    st.session_state.user_id, test_id,
                    0, len(quiz_questions),
                )
            st.session_state.questions = shuffle_question_options(quiz_questions)
            st.session_state.current_index = 0
            st.session_state.score = 0
            st.session_state.answered = False
            st.session_state.show_result = False
            st.session_state.selected_answer = None
            st.session_state.wrong_questions = []
            st.session_state.round_history = []
            st.session_state.current_round = 1
            st.session_state.current_test_id = test_id
            st.session_state.current_session_id = session_id
            st.session_state.active_quiz_level = quiz_level
            st.session_state.quiz_started = True
            st.session_state.page = "Tests"
            st.rerun()


def show_quiz():
    """Show the active quiz flow."""
    questions = st.session_state.questions
    current_index = st.session_state.current_index

    if current_index >= len(questions):
        current_round = st.session_state.get("current_round", 1)
        score = st.session_state.score
        total = len(questions)
        wrong = st.session_state.get("wrong_questions", [])

        # Update session score in DB
        session_id = st.session_state.get("current_session_id")
        if _is_logged_in() and session_id and not st.session_state.get("session_score_saved"):
            update_session_score(session_id, score, total)
            st.session_state.session_score_saved = True

        # Save current round to history if not already saved
        history = st.session_state.get("round_history", [])
        if len(history) < current_round:
            history.append({
                "round": current_round,
                "score": score,
                "total": total,
                "wrong": list(wrong),
            })
            st.session_state.round_history = history

        st.header(t("round_completed"))

        # Current round result
        percentage = (score / total) * 100
        st.subheader(t("round_n", n=current_round))
        st.metric(t("score_label"), f"{score}/{total} ({percentage:.1f}%)")

        if percentage >= 80:
            st.success(t("excellent"))
        elif percentage >= 60:
            st.info(t("good_job"))
        else:
            st.warning(t("keep_practicing"))

        # Accumulated summary across all rounds
        if len(history) > 1:
            st.divider()
            st.subheader(t("accumulated_summary"))
            total_all = sum(r["total"] for r in history)
            correct_all = sum(r["score"] for r in history)
            pct_all = (correct_all / total_all) * 100
            st.metric(t("accumulated_total"), f"{correct_all}/{total_all} ({pct_all:.1f}%)")

            for r in history:
                r_pct = (r["score"] / r["total"]) * 100
                icon = "✓" if r_pct == 100 else "○"
                st.write(f"{icon} **{t('round_n', n=r['round'])}:** {r['score']}/{r['total']} ({r_pct:.1f}%)")

        # Show wrong questions from current round
        if wrong:
            st.divider()
            st.subheader(t("wrong_questions_round", n=len(wrong)))
            for i, q in enumerate(wrong, 1):
                tag_display = q["tag"].replace("_", " ").title()
                with st.expander(f"{i}. {q['question']}"):
                    st.caption(t("topic", name=tag_display))
                    correct = q["options"][q["answer_index"]]
                    st.success(t("correct_answer", answer=correct))
                    st.info(t("explanation", text=q['explanation']))
                    _render_material_refs(q["db_id"], st.session_state.current_test_id)

            col1, col2 = st.columns(2)
            with col1:
                if st.button(t("retry_wrong"), type="primary"):
                    next_round = current_round + 1
                    random.shuffle(wrong)
                    new_session_id = None
                    if _is_logged_in():
                        new_session_id = create_session(
                            st.session_state.user_id,
                            st.session_state.current_test_id,
                            0, len(wrong),
                        )
                    st.session_state.questions = shuffle_question_options(wrong)
                    st.session_state.current_index = 0
                    st.session_state.score = 0
                    st.session_state.answered = False
                    st.session_state.selected_answer = None
                    st.session_state.wrong_questions = []
                    st.session_state.current_round = next_round
                    st.session_state.current_session_id = new_session_id
                    st.session_state.session_score_saved = False
                    st.rerun()
            with col2:
                if st.button(t("back_to_start")):
                    reset_quiz()
                    st.rerun()
        else:
            if st.button(t("back_to_start")):
                reset_quiz()
                st.rerun()
        return

    question = questions[current_index]

    col1, col2 = st.columns([3, 1])
    with col1:
        st.progress((current_index) / len(questions))
    with col2:
        st.write(t("question_n_of", current=current_index + 1, total=len(questions)))

    st.subheader(question["question"])

    tag_display = question["tag"].replace("_", " ").title()
    st.caption(t("topic", name=tag_display))

    is_difficult = st.session_state.get("active_quiz_level") == "difficult"

    if not st.session_state.answered:
        if is_difficult:
            user_text = st.text_input(t("your_answer"), key=f"open_answer_{current_index}")
            if st.button(t("submit_answer"), type="primary", key=f"submit_{current_index}"):
                correct_text = question["options"][question["answer_index"]]
                is_correct = user_text.strip().lower() == correct_text.strip().lower()
                st.session_state.selected_answer = user_text.strip()
                st.session_state.answered = True
                if is_correct:
                    st.session_state.score += 1
                else:
                    st.session_state.wrong_questions.append(question)
                if _is_logged_in():
                    record_answer(
                        st.session_state.user_id,
                        st.session_state.current_test_id,
                        question["id"],
                        is_correct,
                        st.session_state.get("current_session_id"),
                    )
                st.rerun()
        else:
            for i, option in enumerate(question["options"]):
                if st.button(option, key=f"option_{i}", use_container_width=True):
                    st.session_state.selected_answer = i
                    st.session_state.answered = True
                    is_correct = i == question["answer_index"]
                    if is_correct:
                        st.session_state.score += 1
                    else:
                        st.session_state.wrong_questions.append(question)
                    if _is_logged_in():
                        record_answer(
                            st.session_state.user_id,
                            st.session_state.current_test_id,
                            question["id"],
                            is_correct,
                            st.session_state.get("current_session_id"),
                        )
                    st.rerun()

    else:
        correct_index = question["answer_index"]
        correct_text = question["options"][correct_index]

        if is_difficult:
            user_text = st.session_state.selected_answer
            is_correct = user_text.lower() == correct_text.strip().lower()
            if is_correct:
                st.success(t("correct"))
            else:
                st.error(t("incorrect"))
                st.write(f"**{t('your_answer')}** {user_text}")
            st.success(t("correct_answer", answer=correct_text))
        else:
            selected = st.session_state.selected_answer
            for i, option in enumerate(question["options"]):
                if i == correct_index:
                    st.success(f"✓ {option}")
                elif i == selected and selected != correct_index:
                    st.error(f"✗ {option}")
                else:
                    st.write(f"  {option}")

            if selected == correct_index:
                st.success(t("correct"))
            else:
                st.error(t("incorrect"))

        st.info(t("explanation", text=question['explanation']))
        _render_material_refs(question["db_id"], st.session_state.current_test_id)

        if st.button(t("next_question"), type="primary"):
            st.session_state.current_index += 1
            st.session_state.answered = False
            st.session_state.selected_answer = None
            st.rerun()

    st.divider()
    if st.button(t("abandon_test")):
        reset_quiz()
        st.rerun()


def show_dashboard():
    """Show the results dashboard."""
    st.header(t("results_history"))

    user_id = st.session_state.user_id
    sessions = get_user_sessions(user_id)

    if not sessions:
        st.info(t("no_results_yet"))
        return

    # --- Sessions summary ---
    st.subheader(t("previous_sessions"))

    selected_session_ids = []

    for s in sessions:
        test_display = s["title"] or t("unknown_test")
        pct = (s["score"] / s["total"]) * 100 if s["total"] > 0 else 0
        date_str = s["date"][:16] if s["date"] else "—"
        wrong_count = s["total"] - s["score"]

        col1, col2 = st.columns([4, 1])
        with col1:
            label = f"{date_str} — {test_display}: {s['score']}/{s['total']} ({pct:.0f}%)"
            if wrong_count > 0:
                with st.expander(label):
                    wrong_refs = get_session_wrong_answers(s["id"])
                    if wrong_refs:
                        by_test = {}
                        for w in wrong_refs:
                            by_test.setdefault(w["test_id"], set()).add(w["question_id"])
                        wrong_questions = []
                        for tid, q_ids in by_test.items():
                            if tid:
                                wrong_questions.extend(get_test_questions_by_ids(tid, list(q_ids)))
                        for i, q in enumerate(wrong_questions, 1):
                            tag_display = q["tag"].replace("_", " ").title()
                            st.markdown(f"**{i}. {q['question']}**")
                            st.caption(t("topic", name=tag_display))
                            correct = q["options"][q["answer_index"]]
                            st.success(t("correct_answer", answer=correct))
                            st.info(t("explanation", text=q['explanation']))
                            _render_material_refs(q["db_id"], st.session_state.get("current_test_id"))
                            st.write("---")
                    else:
                        st.write(t("no_wrong_details"))
            else:
                st.write(f"{label} ✓")
        with col2:
            if wrong_count > 0:
                if st.checkbox(t("select_checkbox"), key=f"sel_session_{s['id']}", label_visibility="collapsed"):
                    selected_session_ids.append(s["id"])

    # --- Practice from selected sessions ---
    if selected_session_ids:
        st.divider()
        all_wrong = []
        for sid in selected_session_ids:
            wrong_refs = get_session_wrong_answers(sid)
            for w in wrong_refs:
                all_wrong.append(w)

        seen = set()
        unique_wrong = []
        for w in all_wrong:
            key = (w["test_id"], w["question_id"])
            if key not in seen:
                seen.add(key)
                unique_wrong.append(w)

        st.write(t("wrong_selected", n=len(unique_wrong)))
        if st.button(t("practice_wrong"), type="primary"):
            _start_quiz_from_wrong(unique_wrong)


def _start_quiz_from_wrong(wrong_refs):
    """Start a quiz from a list of wrong question references."""
    by_test = {}
    for w in wrong_refs:
        by_test.setdefault(w["test_id"], set()).add(w["question_id"])

    quiz_questions = []
    test_id = None
    for tid, q_ids in by_test.items():
        if tid:
            questions = get_test_questions_by_ids(tid, list(q_ids))
            quiz_questions.extend(questions)
            test_id = tid

    if not quiz_questions:
        return

    random.shuffle(quiz_questions)
    tid = test_id or 0
    session_id = create_session(
        st.session_state.user_id, tid, 0, len(quiz_questions),
    )
    st.session_state.questions = shuffle_question_options(quiz_questions)
    st.session_state.current_index = 0
    st.session_state.score = 0
    st.session_state.answered = False
    st.session_state.show_result = False
    st.session_state.selected_answer = None
    st.session_state.wrong_questions = []
    st.session_state.round_history = []
    st.session_state.current_round = 1
    st.session_state.current_test_id = tid
    st.session_state.current_session_id = session_id
    st.session_state.session_score_saved = False
    st.session_state.quiz_started = True
    st.session_state.page = "Tests"
    st.rerun()


def show_create_test():
    """Show the create test form."""
    st.header(t("create_new_test"))

    if st.button(t("back")):
        st.session_state.page = "Tests"
        st.rerun()

    title = st.text_input(t("test_title"), key="new_test_title")
    description = st.text_area(t("description"), key="new_test_desc")
    language = st.selectbox(
        t("language"), options=LANGUAGE_OPTIONS,
        format_func=lambda x: _lang_display(x) if x else "—",
        key="new_test_lang",
    )

    uploaded_json = st.file_uploader(
        t("import_json"),
        type=["json"],
        key="new_test_json",
    )

    if st.button(t("create_test_btn"), type="primary"):
        if not title.strip():
            st.warning(t("title_required"))
        else:
            author = st.session_state.get("username", "")
            test_id = create_test(st.session_state.user_id, title.strip(), description.strip(), author, language)

            if uploaded_json is not None:
                import json
                try:
                    data = json.loads(uploaded_json.read())
                    if isinstance(data, dict):
                        # Import metadata if not manually set
                        if not title.strip() and data.get("title"):
                            update_test(test_id, data["title"], data.get("description", ""),
                                        data.get("author", ""), data.get("language", ""),
                                        data.get("visibility", "public"))
                        elif data.get("visibility") or data.get("language"):
                            update_test(test_id, title.strip(), description.strip(),
                                        st.session_state.get("username", ""),
                                        data.get("language", language),
                                        data.get("visibility", "public"))

                        # Import materials
                        mat_id_map = {}
                        for mat in data.get("materials", []):
                            old_id = mat.get("id")
                            new_mat_id = add_test_material(
                                test_id, mat.get("material_type", "url"),
                                mat.get("title", ""), mat.get("url", ""),
                                pause_times=mat.get("pause_times", ""),
                                transcript=mat.get("transcript", ""),
                            )
                            if old_id is not None:
                                mat_id_map[old_id] = new_mat_id

                        # Import collaborators
                        for collab in data.get("collaborators", []):
                            email = collab.get("email", "").strip()
                            role = collab.get("role", "guest")
                            if email:
                                add_collaborator(test_id, email, role)

                        questions_list = data.get("questions", [])
                    else:
                        questions_list = data
                        mat_id_map = {}

                    for i, q in enumerate(questions_list, 1):
                        q_id = add_question(
                            test_id, i,
                            q.get("tag", "general"),
                            q["question"],
                            q["options"],
                            q["answer_index"],
                            q.get("explanation", ""),
                            source="json_import",
                        )
                        # Import material references
                        refs = q.get("material_refs", [])
                        if refs and mat_id_map:
                            links = []
                            for ref in refs:
                                new_mid = mat_id_map.get(ref.get("material_id"))
                                if new_mid:
                                    links.append({"material_id": new_mid, "context": ref.get("context", "")})
                            if links:
                                set_question_material_links(q_id, links)
                except (json.JSONDecodeError, KeyError) as e:
                    st.error(t("json_import_error", e=e))

            st.session_state.editing_test_id = test_id
            st.session_state.page = "Editar Test"
            st.rerun()


def show_test_editor():
    """Show the test editor page for editing metadata and questions."""
    test_id = st.session_state.get("editing_test_id")
    if not test_id:
        st.session_state.page = "Tests"
        st.rerun()
        return

    test = get_test(test_id)
    if not test:
        st.error(t("test_not_found"))
        return

    user_id = st.session_state.get("user_id")
    is_owner = test["owner_id"] == user_id
    if is_owner:
        user_role = "owner"
    else:
        user_role = get_user_role_for_test(test_id, user_id)
    if not user_role:
        st.error(t("no_permission"))
        return
    read_only = user_role in ("guest", "student")

    questions = get_test_questions(test_id)

    st.header(t("edit_colon", name=test['title']))

    if st.button(t("back")):
        if "editing_test_id" in st.session_state:
            del st.session_state.editing_test_id
        st.session_state.page = "Tests"
        st.rerun()

    # --- Metadata ---
    meta_disabled = user_role not in ("owner", "admin")
    st.subheader(t("test_info"))
    new_title = st.text_input(t("title"), value=test["title"], key="edit_title", disabled=meta_disabled)
    new_desc = st.text_area(t("description"), value=test["description"] or "", key="edit_desc", disabled=meta_disabled)
    new_author = st.text_input(t("author_label"), value=test["author"] or "", key="edit_author", disabled=meta_disabled)
    current_lang_index = LANGUAGE_OPTIONS.index(test.get("language", "")) if test.get("language", "") in LANGUAGE_OPTIONS else 0
    new_language = st.selectbox(
        t("language"), options=LANGUAGE_OPTIONS,
        index=current_lang_index,
        format_func=lambda x: _lang_display(x) if x else "—",
        key="edit_lang",
        disabled=meta_disabled,
    )
    visibility_options = ["public", "private", "hidden"]
    visibility_labels = {
        "public": t("visibility_public"),
        "private": t("visibility_private"),
        "hidden": t("visibility_hidden"),
    }
    current_vis = test.get("visibility", "public")
    current_vis_index = visibility_options.index(current_vis) if current_vis in visibility_options else 0
    new_visibility = st.selectbox(
        t("visibility"), options=visibility_options,
        index=current_vis_index,
        format_func=lambda x: visibility_labels[x],
        key="edit_visibility",
        disabled=meta_disabled,
    )

    if not meta_disabled:
        if st.button(t("save_info"), type="primary"):
            if not new_title.strip():
                st.warning(t("title_required"))
            else:
                update_test(test_id, new_title.strip(), new_desc.strip(), new_author.strip(), new_language, new_visibility)
                if "editing_test_id" in st.session_state:
                    del st.session_state.editing_test_id
                st.session_state.selected_test = test_id
                st.session_state.page = "Configurar Test"
                st.rerun()

    st.divider()

    materials = get_test_materials(test_id)

    # --- Materials (owner and admin only) ---
    if user_role in ("owner", "admin"):
        st.subheader(t("reference_materials_header"))

        for mat in materials:
            type_icons = {"pdf": "📄", "youtube": "▶️", "image": "🖼️", "url": "🔗"}
            icon = type_icons.get(mat["material_type"], "📎")
            label = mat["title"] or mat["url"] or t("no_title")
            with st.expander(f"{icon} {label}"):
                new_title = st.text_input(t("material_title"), value=mat["title"] or "", key=f"edit_mat_title_{mat['id']}")
                new_url = ""
                if mat["material_type"] in ("youtube", "url"):
                    new_url = st.text_input(t("url"), value=mat["url"] or "", key=f"edit_mat_url_{mat['id']}")
                new_pause = ""
                if mat["material_type"] == "youtube":
                    new_pause = st.text_input(
                        t("pause_times_label"),
                        value=_format_pause_times(mat.get("pause_times", "")),
                        key=f"edit_mat_pause_{mat['id']}",
                        help=t("pause_times_help"),
                    )
                if mat["material_type"] == "image" and mat["file_data"]:
                    st.image(mat["file_data"], width=200)
                elif mat["material_type"] == "pdf" and mat["file_data"]:
                    st.download_button(
                        t("download"),
                        data=mat["file_data"],
                        file_name=f"{label}.pdf",
                        key=f"dl_mat_{mat['id']}",
                    )
                is_yt = mat["material_type"] == "youtube"
                cols = st.columns([1, 1, 1, 1, 1] if is_yt else [1, 1, 1])
                with cols[0]:
                    if st.button(t("save_material"), key=f"save_mat_{mat['id']}", type="primary"):
                        pause_json = _parse_pause_times(new_pause) if is_yt else ""
                        update_test_material(mat["id"], new_title.strip(), new_url.strip(), pause_times=pause_json)
                        st.rerun()
                with cols[1]:
                    if st.button(t("generate"), key=f"gen_mat_{mat['id']}"):
                        next_num = get_next_question_num(test_id)
                        mat_label = mat["title"] or t("no_title")
                        for i in range(3):
                            add_question(
                                test_id, next_num + i, "general",
                                t("generated_question", name=mat_label, n=i+1),
                                [t("option_a"), t("option_b"), t("option_c"), t("option_d")],
                                0, t("generated_explanation"),
                                source=f"material:{mat['id']}",
                            )
                        st.rerun()
                if is_yt:
                    with cols[2]:
                        if st.button(f"📜 {t('transcript')}", key=f"transcript_mat_{mat['id']}"):
                            transcript_text = mat.get("transcript", "")
                            if not transcript_text:
                                transcript_text = _fetch_youtube_transcript(mat["url"])
                                if transcript_text:
                                    update_material_transcript(mat["id"], transcript_text)
                            if transcript_text:
                                _show_transcript_dialog(transcript_text)
                            else:
                                st.warning(t("no_transcript"))
                    with cols[3]:
                        if st.button(f"🏷️ {t('generate_topics_btn')}", key=f"gen_topics_mat_{mat['id']}"):
                            transcript_text = mat.get("transcript", "")
                            if not transcript_text:
                                transcript_text = _fetch_youtube_transcript(mat["url"])
                                if transcript_text:
                                    update_material_transcript(mat["id"], transcript_text)
                            if transcript_text:
                                existing_tags = get_test_tags(test_id)
                                _show_generate_topics_dialog(test_id, transcript_text, existing_tags)
                            else:
                                st.warning(t("no_transcript"))
                with cols[-1]:
                    if st.button("🗑️", key=f"del_mat_{mat['id']}"):
                        delete_test_material(mat["id"])
                        st.rerun()

        st.write(t("add_material_label"))
        mat_type = st.selectbox(t("material_type"), ["pdf", "youtube", "image", "url"],
                                format_func=lambda x: {"pdf": t("pdf"), "youtube": t("youtube"), "image": t("image"), "url": t("url_type")}[x],
                                key="new_mat_type")
        mat_title = st.text_input(t("material_title"), key="new_mat_title")

        mat_url = ""
        mat_file = None
        mat_pause_times = ""
        if mat_type in ("youtube", "url"):
            mat_url = st.text_input(t("url"), key="new_mat_url")
            if mat_type == "youtube":
                mat_pause_times = st.text_input(t("pause_times_label"), key="new_mat_pause_times", help=t("pause_times_help"))
        else:
            file_types = ["pdf"] if mat_type == "pdf" else ["png", "jpg", "jpeg", "gif"]
            mat_file = st.file_uploader(t("file"), type=file_types, key="new_mat_file")

        if st.button(t("add_material_btn"), type="secondary"):
            file_data = mat_file.read() if mat_file else None
            if mat_type in ("youtube", "url") and not mat_url.strip():
                st.warning(t("url_required"))
            elif mat_type in ("pdf", "image") and not file_data:
                st.warning(t("file_required"))
            else:
                pause_json = _parse_pause_times(mat_pause_times) if mat_type == "youtube" else ""
                transcript = ""
                if mat_type == "youtube" and mat_url.strip():
                    transcript = _fetch_youtube_transcript(mat_url.strip())
                add_test_material(test_id, mat_type, mat_title.strip(), mat_url.strip(), file_data,
                                  pause_times=pause_json, transcript=transcript)
                st.rerun()

        st.divider()

    # --- Topics (owner and admin only) ---
    if user_role in ("owner", "admin"):
        st.subheader(t("topics"))

        tags = get_test_tags(test_id)
        tag_counts = {}
        for q in questions:
            tag_counts[q["tag"]] = tag_counts.get(q["tag"], 0) + 1

        tag_edits = {}
        for tag in tags:
            count = tag_counts.get(tag, 0)
            confirm_key = f"confirm_del_tag_{tag}"

            if st.session_state.get(confirm_key):
                st.warning(t("delete_topic_confirm", tag=tag, n=count))
                col_del_q, col_blank, col_cancel = st.columns(3)
                with col_del_q:
                    if st.button(t("delete_questions_btn"), key=f"deltag_delq_{tag}"):
                        delete_test_tag(test_id, tag, delete_questions=True)
                        del st.session_state[confirm_key]
                        st.rerun()
                with col_blank:
                    if st.button(t("leave_blank"), key=f"deltag_blank_{tag}"):
                        delete_test_tag(test_id, tag, delete_questions=False)
                        del st.session_state[confirm_key]
                        st.rerun()
                with col_cancel:
                    if st.button(t("cancel"), key=f"deltag_cancel_{tag}"):
                        del st.session_state[confirm_key]
                        st.rerun()
            else:
                col_name, col_count, col_del = st.columns([3, 1, 0.5])
                with col_name:
                    new_name = st.text_input(t("topic_label"), value=tag, key=f"tag_name_{tag}", label_visibility="collapsed")
                    tag_edits[tag] = new_name
                with col_count:
                    st.caption(t("n_questions_abbrev", n=count))
                with col_del:
                    if st.button("🗑️", key=f"del_tag_{tag}"):
                        st.session_state[confirm_key] = True
                        st.rerun()

        if tag_edits:
            if st.button(t("save_topic_changes")):
                for old_tag, new_tag in tag_edits.items():
                    if new_tag.strip() != old_tag and new_tag.strip():
                        rename_test_tag(test_id, old_tag, new_tag.strip())
                st.rerun()

        st.write(t("add_topic_label"))
        col_new_tag, col_add_tag = st.columns([3, 1])
        with col_new_tag:
            new_tag_name = st.text_input(t("topic_label"), key="new_tag_name", label_visibility="collapsed", placeholder=t("topic_name_placeholder"))
        with col_add_tag:
            if st.button(t("add_btn")):
                if new_tag_name and new_tag_name.strip():
                    next_num = get_next_question_num(test_id)
                    add_question(test_id, next_num, new_tag_name.strip(), t("new_question_text"), [t("option_a"), t("option_b"), t("option_c"), t("option_d")], 0, "")
                    st.rerun()

        st.divider()

    # --- Questions ---
    with st.expander(t("questions_header", n=len(questions)), expanded=False):
        if not read_only:
            if st.button(t("add_question")):
                next_num = get_next_question_num(test_id)
                add_question(test_id, next_num, "general", t("new_question_text"), [t("option_a"), t("option_b"), t("option_c"), t("option_d")], 0, "")
                st.rerun()

        # Pre-load all question-material links
        all_q_db_ids = [q["db_id"] for q in questions]
        all_q_mat_links = get_question_material_links_bulk(all_q_db_ids) if all_q_db_ids else {}
        mat_by_id = {m["id"]: m for m in materials}

        for q in questions:
            with st.expander(f"#{q['id']} — {q['question'][:80]}"):
                q_key = f"q_{q['db_id']}"
                source = q.get("source", "manual")
                if source == "manual":
                    source_label = t("source_manual")
                elif source == "json_import":
                    source_label = t("source_json")
                elif source.startswith("material:"):
                    source_label = t("source_material", id=source.split(':')[1])
                else:
                    source_label = source
                st.caption(t("source", name=source_label))
                q_tag = st.text_input(t("topic_label"), value=q["tag"], key=f"{q_key}_tag", disabled=read_only)
                q_text = st.text_area(t("question_label"), value=q["question"], key=f"{q_key}_text", disabled=read_only)
                q_explanation = st.text_area(t("explanation_label"), value=q.get("explanation", ""), key=f"{q_key}_expl", disabled=read_only)

                st.write(t("options_header"))
                options = []
                for oi in range(len(q["options"])):
                    opt = st.text_input(t("option_n", n=oi + 1), value=q["options"][oi], key=f"{q_key}_opt_{oi}", disabled=read_only)
                    options.append(opt)

                if not read_only:
                    col_add, col_rm = st.columns(2)
                    with col_add:
                        if st.button(t("add_option"), key=f"{q_key}_add_opt"):
                            new_opts = q["options"] + [t("option_n", n=len(q['options']) + 1)]
                            update_question(q["db_id"], q["tag"], q["question"], new_opts, q["answer_index"], q.get("explanation", ""))
                            st.rerun()
                    with col_rm:
                        if len(q["options"]) > 2:
                            if st.button(t("remove_option"), key=f"{q_key}_rm_opt"):
                                new_opts = q["options"][:-1]
                                new_ans = min(q["answer_index"], len(new_opts) - 1)
                                update_question(q["db_id"], q["tag"], q["question"], new_opts, new_ans, q.get("explanation", ""))
                                st.rerun()

                q_answer = st.selectbox(
                    t("correct_answer_select"),
                    range(len(options)),
                    index=q["answer_index"],
                    format_func=lambda i: options[i] if i < len(options) else "",
                    key=f"{q_key}_ans",
                    disabled=read_only,
                )

                # --- Material references ---
                if materials and not read_only:
                    st.write(t("material_references"))
                    existing_links = {lk["material_id"]: lk["context"] for lk in all_q_mat_links.get(q["db_id"], [])}
                    q_mat_links = {}
                    for mat in materials:
                        mid = mat["id"]
                        type_icons = {"pdf": "📄", "youtube": "▶️", "image": "🖼️", "url": "🔗"}
                        icon = type_icons.get(mat["material_type"], "📎")
                        mlabel = mat["title"] or mat["url"] or t("no_title")
                        is_linked = st.checkbox(f"{icon} {mlabel}", value=mid in existing_links, key=f"{q_key}_mat_{mid}")
                        if is_linked:
                            ctx = existing_links.get(mid, "")
                            if mat["material_type"] == "youtube":
                                ctx = st.text_input(t("timestamps_hint"), value=ctx, key=f"{q_key}_mat_ctx_{mid}")
                            elif mat["material_type"] == "pdf":
                                ctx = st.text_input(t("pages_hint"), value=ctx, key=f"{q_key}_mat_ctx_{mid}")
                            q_mat_links[mid] = ctx

                if not read_only:
                    col_save, col_del = st.columns(2)
                    with col_save:
                        if st.button(t("save_question"), key=f"{q_key}_save", type="primary"):
                            update_question(q["db_id"], q_tag.strip(), q_text.strip(), options, q_answer, q_explanation.strip())
                            if materials:
                                links = [{"material_id": mid, "context": ctx} for mid, ctx in q_mat_links.items()]
                                set_question_material_links(q["db_id"], links)
                            st.success(t("question_updated"))
                            st.rerun()
                    with col_del:
                        if st.button(t("delete_question"), key=f"{q_key}_del"):
                            delete_question(q["db_id"])
                            st.rerun()

    st.divider()

    # --- Collaborators ---
    if user_role in ("owner", "admin"):
        st.subheader(t("collaborators"))
        collabs = get_collaborators(test_id)
        if collabs:
            for c in collabs:
                col_email, col_role, col_del = st.columns([3, 2, 0.5])
                with col_email:
                    st.write(c["email"])
                with col_role:
                    role_options = ["student", "guest", "reviewer", "admin"]
                    role_labels = {"student": t("role_student"), "guest": t("role_guest"), "reviewer": t("role_reviewer"), "admin": t("role_admin")}
                    new_role = st.selectbox(
                        t("role_label"), options=role_options, index=role_options.index(c["role"]),
                        format_func=lambda x: role_labels[x], key=f"collab_role_{c['id']}",
                    )
                    if new_role != c["role"]:
                        update_collaborator_role(test_id, c["email"], new_role)
                        st.rerun()
                with col_del:
                    if st.button("🗑️", key=f"del_collab_{c['id']}"):
                        remove_collaborator(test_id, c["email"])
                        st.success(t("collaborator_removed"))
                        st.rerun()

        st.write(t("invite_user"))
        col_inv_email, col_inv_role, col_inv_btn = st.columns([3, 2, 1])
        with col_inv_email:
            inv_email = st.text_input(t("email_placeholder"), key="invite_email", label_visibility="collapsed", placeholder=t("email_placeholder"))
        with col_inv_role:
            inv_role_options = ["student", "guest", "reviewer", "admin"]
            inv_role_labels = {"student": t("role_student"), "guest": t("role_guest"), "reviewer": t("role_reviewer"), "admin": t("role_admin")}
            inv_role = st.selectbox(t("role_label"), options=inv_role_options, format_func=lambda x: inv_role_labels[x], key="invite_role", label_visibility="collapsed")
        with col_inv_btn:
            if st.button(t("invite_btn"), type="secondary"):
                if not inv_email.strip():
                    st.warning(t("email_required"))
                elif inv_email.strip() == st.session_state.get("username"):
                    st.warning(t("cannot_invite_self"))
                else:
                    add_collaborator(test_id, inv_email.strip(), inv_role)
                    st.success(t("collaborator_added"))
                    st.rerun()

    st.divider()

    # --- Delete test (owner only) ---
    if user_role == "owner":
        st.subheader(t("danger_zone"))
        if st.button(t("delete_full_test"), type="secondary"):
            st.session_state[f"confirm_delete_{test_id}"] = True

        if st.session_state.get(f"confirm_delete_{test_id}"):
            st.warning(t("confirm_delete"))
            col_yes, col_no = st.columns(2)
            with col_yes:
                if st.button(t("yes_delete"), type="primary"):
                    delete_test(test_id)
                    if "editing_test_id" in st.session_state:
                        del st.session_state.editing_test_id
                    st.session_state.page = "Tests"
                    st.rerun()
            with col_no:
                if st.button(t("cancel")):
                    del st.session_state[f"confirm_delete_{test_id}"]
                    st.rerun()


def _get_avatar_html(avatar_bytes, size=35):
    """Return HTML for a circular avatar image, or initials if no avatar."""
    if avatar_bytes:
        b64 = base64.b64encode(avatar_bytes).decode()
        return f'<img src="data:image/png;base64,{b64}" style="width:{size}px;height:{size}px;border-radius:50%;object-fit:cover;">'
    initial = st.session_state.get("username", "?")[0].upper()
    return (
        f'<div style="width:{size}px;height:{size}px;border-radius:50%;'
        f'background:#4A90D9;color:white;display:flex;align-items:center;'
        f'justify-content:center;font-size:{size//2}px;font-weight:bold;">'
        f'{initial}</div>'
    )


def _load_profile_to_session():
    """Load user profile from DB into session state if not cached."""
    if "profile_loaded" not in st.session_state:
        profile = get_user_profile(st.session_state.user_id)
        st.session_state.display_name = profile["display_name"] or st.session_state.username
        st.session_state.avatar_bytes = profile["avatar"]
        st.session_state.profile_loaded = True


def show_profile():
    """Show profile settings page."""
    st.header(t("profile_header"))

    profile = get_user_profile(st.session_state.user_id)
    current_name = profile["display_name"] or st.session_state.username
    current_avatar = profile["avatar"]

    if current_avatar:
        st.image(current_avatar, width=120)
    else:
        st.markdown(_get_avatar_html(None, size=120), unsafe_allow_html=True)

    st.divider()

    display_name = st.text_input(t("display_name"), value=current_name, key="profile_name_input")

    uploaded_file = st.file_uploader(
        t("upload_photo"),
        type=["png", "jpg", "jpeg"],
        key="profile_avatar_upload",
    )

    if st.button(t("save"), type="primary"):
        avatar_data = None
        if uploaded_file is not None:
            avatar_data = uploaded_file.read()
        elif current_avatar:
            avatar_data = current_avatar

        if avatar_data is not None:
            update_user_profile(st.session_state.user_id, display_name, avatar_data)
        else:
            update_user_profile(st.session_state.user_id, display_name)

        st.session_state.display_name = display_name
        st.session_state.avatar_bytes = avatar_data
        st.session_state.username = display_name
        st.success(t("profile_updated"))
        st.session_state.page = st.session_state.get("prev_page", "Tests")
        st.rerun()


def _render_program_card(prog, user_id, has_access=True, prefix=""):
    """Render a single program card."""
    with st.container(border=True):
        col_info, col_btn = st.columns([4, 1])
        with col_info:
            title_display = prog["title"]
            if prog.get("visibility") == "private" and not has_access:
                title_display = "🔒 " + title_display
            st.subheader(title_display)
            if prog.get("description"):
                st.write(prog["description"])
            st.caption(t("n_tests", n=prog['test_count']))
        with col_btn:
            if has_access:
                if st.button(t("select"), key=f"{prefix}prog_sel_{prog['id']}", use_container_width=True):
                    st.session_state.selected_program = prog["id"]
                    st.session_state.page = "Configurar Programa"
                    st.rerun()
            else:
                st.button(t("select"), key=f"{prefix}prog_sel_{prog['id']}", use_container_width=True, disabled=True)
            is_owner = prog.get("owner_id") == user_id
            prog_role = get_user_role_for_program(prog["id"], user_id) if not is_owner else None
            can_edit = is_owner or prog_role in ("guest", "reviewer", "admin")
            if can_edit:
                if st.button(t("edit"), key=f"{prefix}prog_edit_{prog['id']}", use_container_width=True):
                    st.session_state.editing_program_id = prog["id"]
                    st.session_state.page = "Editar Programa"
                    st.rerun()


def show_programs():
    """Show the program catalog."""
    user_id = st.session_state.user_id
    programs = get_all_programs(user_id)
    shared_programs = get_shared_programs(user_id)
    shared_prog_ids = {p["id"] for p in shared_programs}

    st.header(t("programs_header"))

    if st.button(t("create_program"), type="secondary"):
        st.session_state.page = "Crear Programa"
        st.rerun()

    # Build accessible set
    accessible_ids = shared_prog_ids | {p["id"] for p in programs if p.get("owner_id") == user_id}

    def _prog_has_access(p):
        if p.get("visibility", "public") == "public":
            return True
        return p["id"] in accessible_ids

    my_progs = [p for p in programs if p.get("owner_id") == user_id]
    other_progs = [p for p in programs if p.get("owner_id") != user_id and p["id"] not in shared_prog_ids]

    if my_progs:
        st.subheader(t("my_programs"))
        for prog in my_progs:
            _render_program_card(prog, user_id, has_access=True, prefix="my_")

    if shared_programs:
        st.subheader(t("shared_programs"))
        for prog in shared_programs:
            _render_program_card(prog, user_id, has_access=True, prefix="shared_")

    if other_progs:
        if my_progs or shared_programs:
            st.subheader(t("all_programs"))
        for prog in other_progs:
            _render_program_card(prog, user_id, has_access=_prog_has_access(prog))

    if not programs and not shared_programs:
        st.info(t("no_programs"))


def show_create_program():
    """Show create program form."""
    st.header(t("create_new_program"))

    if st.button(t("back")):
        st.session_state.page = "Programas"
        st.rerun()

    title = st.text_input(t("program_title"), key="new_prog_title")
    description = st.text_area(t("description"), key="new_prog_desc")

    if st.button(t("create_program_btn"), type="primary"):
        if not title.strip():
            st.warning(t("title_required"))
        else:
            program_id = create_program(st.session_state.user_id, title.strip(), description.strip())
            st.session_state.editing_program_id = program_id
            st.session_state.page = "Editar Programa"
            st.rerun()


def show_program_editor():
    """Show program editor page."""
    program_id = st.session_state.get("editing_program_id")
    if not program_id:
        st.session_state.page = "Programas"
        st.rerun()
        return

    prog = get_program(program_id)
    if not prog:
        st.error(t("program_not_found"))
        return

    user_id = st.session_state.get("user_id")
    is_owner = prog["owner_id"] == user_id
    if is_owner:
        prog_role = "owner"
    else:
        prog_role = get_user_role_for_program(program_id, user_id)
    if not prog_role:
        st.error(t("no_permission"))
        return
    read_only = prog_role in ("guest", "student")
    meta_disabled = prog_role not in ("owner", "admin")

    st.header(t("edit_colon", name=prog['title']))

    if st.button(t("back")):
        if "editing_program_id" in st.session_state:
            del st.session_state.editing_program_id
        st.session_state.page = "Programas"
        st.rerun()

    # --- Metadata ---
    st.subheader(t("program_info"))
    new_title = st.text_input(t("title"), value=prog["title"], key="edit_prog_title", disabled=meta_disabled)
    new_desc = st.text_area(t("description"), value=prog["description"] or "", key="edit_prog_desc", disabled=meta_disabled)

    visibility_options = ["public", "private", "hidden"]
    visibility_labels = {
        "public": t("visibility_public"),
        "private": t("visibility_private"),
        "hidden": t("visibility_hidden"),
    }
    current_vis = prog.get("visibility", "public")
    current_vis_index = visibility_options.index(current_vis) if current_vis in visibility_options else 0
    new_visibility = st.selectbox(
        t("visibility"), options=visibility_options,
        index=current_vis_index,
        format_func=lambda x: visibility_labels[x],
        key="edit_prog_visibility",
        disabled=meta_disabled,
    )

    if not meta_disabled:
        if st.button(t("save_info"), type="primary"):
            if not new_title.strip():
                st.warning(t("title_required"))
            else:
                update_program(program_id, new_title.strip(), new_desc.strip(), new_visibility)
                st.success(t("info_updated"))
                st.rerun()

    st.divider()

    # --- Tests in program ---
    st.subheader(t("tests_included"))

    prog_tests = get_program_tests(program_id)
    if prog_tests:
        for pt in prog_tests:
            if not meta_disabled:
                col_info, col_rm = st.columns([5, 1])
                with col_info:
                    st.write(f"**{pt['title']}** ({t('n_questions', n=pt['question_count'])})")
                with col_rm:
                    if st.button(t("remove"), key=f"rm_pt_{pt['id']}"):
                        remove_test_from_program(program_id, pt["id"])
                        st.rerun()
            else:
                st.write(f"**{pt['title']}** ({t('n_questions', n=pt['question_count'])})")
    else:
        st.info(t("no_tests_in_program"))

    # Add test (owner and admin only)
    if not meta_disabled:
        all_tests = get_all_tests(st.session_state.user_id)
        current_test_ids = {pt["id"] for pt in prog_tests}
        available_tests = [tt for tt in all_tests if tt["id"] not in current_test_ids]

        if available_tests:
            st.write(t("add_test_label"))
            test_options = {tt["id"]: f"{tt['title']} ({t('n_questions_abbrev', n=tt['question_count'])})" for tt in available_tests}
            selected_test_id = st.selectbox(
                t("test_label"), options=list(test_options.keys()),
                format_func=lambda x: test_options[x],
                key="add_prog_test",
            )
            if st.button(t("add_test_btn")):
                add_test_to_program(program_id, selected_test_id)
                st.rerun()

    st.divider()

    # --- Collaborators (owner and admin only) ---
    if prog_role in ("owner", "admin"):
        st.subheader(t("collaborators"))
        collabs = get_program_collaborators(program_id)
        if collabs:
            for c in collabs:
                col_email, col_role, col_del = st.columns([3, 2, 0.5])
                with col_email:
                    st.write(c["email"])
                with col_role:
                    role_options = ["student", "guest", "reviewer", "admin"]
                    role_labels = {"student": t("role_student"), "guest": t("role_guest"), "reviewer": t("role_reviewer"), "admin": t("role_admin")}
                    new_role = st.selectbox(
                        t("role_label"), options=role_options, index=role_options.index(c["role"]),
                        format_func=lambda x: role_labels[x], key=f"prog_collab_role_{c['id']}",
                    )
                    if new_role != c["role"]:
                        update_program_collaborator_role(program_id, c["email"], new_role)
                        st.rerun()
                with col_del:
                    if st.button("🗑️", key=f"del_prog_collab_{c['id']}"):
                        remove_program_collaborator(program_id, c["email"])
                        st.success(t("collaborator_removed"))
                        st.rerun()

        st.write(t("invite_user"))
        col_inv_email, col_inv_role, col_inv_btn = st.columns([3, 2, 1])
        with col_inv_email:
            inv_email = st.text_input(t("email_placeholder"), key="prog_invite_email", label_visibility="collapsed", placeholder=t("email_placeholder"))
        with col_inv_role:
            inv_role_options = ["student", "guest", "reviewer", "admin"]
            inv_role_labels = {"student": t("role_student"), "guest": t("role_guest"), "reviewer": t("role_reviewer"), "admin": t("role_admin")}
            inv_role = st.selectbox(t("role_label"), options=inv_role_options, format_func=lambda x: inv_role_labels[x], key="prog_invite_role", label_visibility="collapsed")
        with col_inv_btn:
            if st.button(t("invite_btn"), key="prog_invite_btn", type="secondary"):
                if not inv_email.strip():
                    st.warning(t("email_required"))
                elif inv_email.strip() == st.session_state.get("username"):
                    st.warning(t("cannot_invite_self"))
                else:
                    add_program_collaborator(program_id, inv_email.strip(), inv_role)
                    st.success(t("collaborator_added"))
                    st.rerun()

    st.divider()

    # --- Delete program (owner only) ---
    if prog_role == "owner":
        st.subheader(t("danger_zone"))
        if st.button(t("delete_program"), type="secondary"):
            st.session_state[f"confirm_delete_prog_{program_id}"] = True

        if st.session_state.get(f"confirm_delete_prog_{program_id}"):
            st.warning(t("confirm_delete"))
            col_yes, col_no = st.columns(2)
            with col_yes:
                if st.button(t("yes_delete"), key="prog_del_yes", type="primary"):
                    delete_program(program_id)
                    if "editing_program_id" in st.session_state:
                        del st.session_state.editing_program_id
                    st.session_state.page = "Programas"
                    st.rerun()
            with col_no:
                if st.button(t("cancel"), key="prog_del_no"):
                    del st.session_state[f"confirm_delete_prog_{program_id}"]
                    st.rerun()


def show_program_config():
    """Show configuration for a program before starting quiz."""
    program_id = st.session_state.get("selected_program")
    if not program_id:
        st.session_state.page = "Programas"
        st.rerun()
        return

    prog = get_program(program_id)
    if not prog:
        st.error(t("program_not_found"))
        return

    # Access control for private/hidden programs
    visibility = prog.get("visibility", "public")
    if visibility != "public":
        logged_in_uid = st.session_state.get("user_id")
        has_prog_access = (
            logged_in_uid and (
                prog["owner_id"] == logged_in_uid
                or get_user_role_for_program(program_id, logged_in_uid) is not None
            )
        )
        if not has_prog_access:
            st.error(t("program_private_no_access"))
            if st.button(t("back_to_programs")):
                if "selected_program" in st.session_state:
                    del st.session_state.selected_program
                st.session_state.page = "Programas"
                st.rerun()
            return

    questions = get_program_questions(program_id)
    tags = get_program_tags(program_id)
    prog_tests = get_program_tests(program_id)

    st.header(prog["title"])
    if prog.get("description"):
        st.write(prog["description"])

    st.caption(t("n_tests_n_questions", nt=len(prog_tests), nq=len(questions)))

    with st.expander(t("tests_included")):
        for pt in prog_tests:
            st.write(f"- **{pt['title']}** ({t('n_questions', n=pt['question_count'])})")

    if st.button(t("back_to_programs")):
        if "selected_program" in st.session_state:
            del st.session_state.selected_program
        st.session_state.page = "Programas"
        st.rerun()

    if not questions:
        st.warning(t("no_program_questions"))
        return

    st.subheader(t("configuration"))

    num_questions = st.number_input(
        t("num_questions"),
        min_value=1,
        max_value=len(questions),
        value=min(25, len(questions)),
    )

    st.write(t("topics_to_include"))
    selected_tags = []
    cols = st.columns(2)
    for i, tag in enumerate(tags):
        tag_display = tag.replace("_", " ").title()
        if cols[i % 2].checkbox(tag_display, value=True, key=f"prog_tag_{tag}"):
            selected_tags.append(tag)

    if not selected_tags:
        st.warning(t("select_at_least_one_topic"))
    else:
        filtered_count = len([q for q in questions if q["tag"] in selected_tags])
        st.info(t("available_questions_with_topics", n=filtered_count))

        if st.button(t("start_test"), type="primary"):
            stats = get_question_stats(st.session_state.user_id, program_id) if _is_logged_in() else None
            quiz_questions = select_balanced_questions(
                questions, selected_tags, num_questions, stats
            )
            session_id = None
            if _is_logged_in():
                session_id = create_session(
                    st.session_state.user_id, None,
                    0, len(quiz_questions),
                )
            st.session_state.questions = shuffle_question_options(quiz_questions)
            st.session_state.current_index = 0
            st.session_state.score = 0
            st.session_state.answered = False
            st.session_state.show_result = False
            st.session_state.selected_answer = None
            st.session_state.wrong_questions = []
            st.session_state.round_history = []
            st.session_state.current_round = 1
            st.session_state.current_test_id = 0
            st.session_state.current_session_id = session_id
            st.session_state.quiz_started = True
            st.session_state.page = "Programas"
            st.rerun()


def main():
    st.set_page_config(page_title="Knowting", page_icon="📚")

    _try_login()

    if _is_logged_in():
        _load_profile_to_session()

    if "page" not in st.session_state:
        st.session_state.page = "Tests"

    logged_in = _is_logged_in()

    # Top bar: title + avatar/login
    col_title, col_avatar = st.columns([6, 1])
    with col_title:
        st.title("Knowting")
        st.subheader(f"*{t('tagline')}*")
    with col_avatar:
        if logged_in:
            avatar_bytes = st.session_state.get("avatar_bytes")
            display_name = st.session_state.get("display_name", st.session_state.username)
            popover_label = "👤"
            with st.popover(popover_label):
                if avatar_bytes:
                    st.image(avatar_bytes, width=60)
                st.write(f"**{display_name}**")
                st.divider()
                if st.button(t("profile"), key="menu_profile", use_container_width=True):
                    st.session_state.prev_page = st.session_state.page
                    st.session_state.page = "Perfil"
                    st.rerun()
                if st.button(t("logout"), key="menu_logout", use_container_width=True):
                    for key in list(st.session_state.keys()):
                        del st.session_state[key]
                    st.logout()
                    st.rerun()
        else:
            st.button(t("login"), on_click=st.login, type="secondary")

    # Sidebar navigation
    with st.sidebar:
        # Language toggle
        current_lang = st.session_state.get("lang", "es")
        current_idx = UI_LANGUAGES.index(current_lang) if current_lang in UI_LANGUAGES else 0
        selected_ui_lang = st.selectbox(
            "🌐", options=UI_LANGUAGES,
            index=current_idx,
            format_func=lambda x: UI_LANG_LABELS.get(x, x),
            key="lang_toggle",
            label_visibility="collapsed",
        )
        if selected_ui_lang != current_lang:
            st.session_state.lang = selected_ui_lang
            st.rerun()

        st.markdown("---")
        nav_items = [("📝", "Tests", t("tests"))]
        if logged_in:
            nav_items.append(("📊", "Dashboard", t("dashboard")))
            nav_items.append(("📚", "Programas", t("programs")))
        for icon, page_id, display in nav_items:
            is_active = st.session_state.page == page_id
            btn_type = "primary" if is_active else "secondary"
            if st.button(f"{icon}  {display}", key=f"nav_{page_id}", use_container_width=True, type=btn_type):
                st.session_state.page = page_id
                st.rerun()
        st.markdown("---")

    if "quiz_started" not in st.session_state:
        st.session_state.quiz_started = False

    if logged_in and st.session_state.page == "Perfil":
        show_profile()
    elif logged_in and st.session_state.page == "Dashboard" and not st.session_state.quiz_started:
        show_dashboard()
    elif st.session_state.page == "Configurar Test":
        show_test_config()
    elif logged_in and st.session_state.page == "Crear Test":
        show_create_test()
    elif logged_in and st.session_state.page == "Editar Test":
        show_test_editor()
    elif logged_in and st.session_state.page == "Programas" and not st.session_state.quiz_started:
        show_programs()
    elif logged_in and st.session_state.page == "Crear Programa":
        show_create_program()
    elif logged_in and st.session_state.page == "Editar Programa":
        show_program_editor()
    elif logged_in and st.session_state.page == "Configurar Programa":
        show_program_config()
    elif st.session_state.quiz_started:
        show_quiz()
    else:
        show_test_catalog()


if __name__ == "__main__":
    main()
