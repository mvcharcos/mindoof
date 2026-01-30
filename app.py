import streamlit as st
import random
import base64
from db import (
    init_db, get_or_create_google_user, record_answer, get_question_stats,
    create_session, update_session_score, get_user_sessions,
    get_session_wrong_answers,
    get_user_profile, update_user_profile,
    toggle_favorite, get_favorite_tests,
    get_all_tests, get_test, get_test_questions, get_test_questions_by_ids,
    get_test_tags, create_test, update_test, delete_test,
    add_question, update_question, delete_question, get_next_question_num,
)

init_db()


def _is_logged_in():
    """Return True if user is authenticated."""
    return bool(st.session_state.get("user_id"))


def _try_login():
    """Attempt to log in the user silently, supporting both st.user and st.experimental_user."""
    if st.session_state.get("user_id"):
        return

    # 1. Identify which authentication object is available
    # We check st.user first (modern), then fallback to st.experimental_user
    user_info = getattr(st, "user", getattr(st, "experimental_user", None))

    # 2. Safely check if the object exists and has the 'is_logged_in' attribute
    if user_info and hasattr(user_info, "is_logged_in"):
        try:
            if user_info.is_logged_in:
                email = user_info.email
                # Use name if available, otherwise fallback to email
                name = getattr(user_info, "name", email) or email
                
                user_id = get_or_create_google_user(email, name)
                st.session_state.user_id = user_id
                st.session_state.username = name
        except Exception as e:
            # This catches cases where the attribute exists but auth isn't fully configured
            st.warning(f"Autenticación disponible pero no configurada: {e}")


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


def reset_quiz():
    """Reset quiz state."""
    for key in ["quiz_started", "questions", "current_index", "answered",
                "score", "show_result", "selected_answer", "wrong_questions",
                "round_history", "current_round", "current_test_id",
                "current_session_id", "session_score_saved"]:
        if key in st.session_state:
            del st.session_state[key]


def _render_test_card(test, favorites, prefix=""):
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
            st.subheader(test["title"])
            if test.get("description"):
                st.write(test["description"])
            meta = f"{test['question_count']} preguntas"
            if test.get("author"):
                meta += f"  ·  Autor: {test['author']}"
            st.caption(meta)
        with col_btn:
            if st.button("Seleccionar", key=f"{prefix}select_{test_id}", use_container_width=True):
                st.session_state.selected_test = test_id
                st.session_state.page = "Configurar Test"
                st.rerun()


def show_test_catalog():
    """Show a searchable catalog of available tests."""
    user_id = st.session_state.get("user_id")
    all_tests = get_all_tests(user_id)

    if not all_tests:
        st.error("No hay tests disponibles.")
        return

    st.header("Tests disponibles")

    search = st.text_input("Buscar test:", placeholder="Escribe para filtrar...", key="test_search")

    logged_in = _is_logged_in()
    favorites = get_favorite_tests(st.session_state.user_id) if logged_in else set()

    if logged_in:
        if st.button("➕ Crear Test", type="secondary"):
            st.session_state.page = "Crear Test"
            st.rerun()

    filtered_tests = [
        t for t in all_tests
        if not search or search.lower() in t["title"].lower()
    ]

    if not filtered_tests:
        st.info("No se encontraron tests con ese criterio.")
        return

    # Favorite tests section
    fav_tests = [t for t in filtered_tests if t["id"] in favorites]
    other_tests = [t for t in filtered_tests if t["id"] not in favorites]

    if fav_tests:
        st.subheader("Favoritos")
        for test in fav_tests:
            _render_test_card(test, favorites, prefix="fav_")

    if other_tests:
        if fav_tests:
            st.subheader("Todos los tests")
        for test in other_tests:
            _render_test_card(test, favorites)


def show_test_config():
    """Show configuration for the selected test before starting."""
    test_id = st.session_state.get("selected_test")
    if not test_id:
        st.session_state.page = "Tests"
        st.rerun()
        return

    test = get_test(test_id)
    if not test:
        st.error("Test no encontrado.")
        return

    questions = get_test_questions(test_id)
    tags = get_test_tags(test_id)

    st.header(test["title"])
    if test.get("description"):
        st.write(test["description"])
    if test.get("author"):
        st.caption(f"Autor: {test['author']}")

    col_back, col_edit = st.columns([1, 1])
    with col_back:
        if st.button("← Volver a tests"):
            del st.session_state.selected_test
            st.session_state.page = "Tests"
            st.rerun()
    with col_edit:
        if _is_logged_in() and (test["owner_id"] == st.session_state.user_id or test["owner_id"] is None):
            if st.button("✏️ Editar test"):
                st.session_state.editing_test_id = test_id
                st.session_state.page = "Editar Test"
                st.rerun()

    st.subheader("Configuracion")

    num_questions = st.number_input(
        "Numero de preguntas:",
        min_value=1,
        max_value=len(questions),
        value=min(25, len(questions))
    )

    st.write("**Temas a incluir:**")
    selected_tags = []
    cols = st.columns(2)
    for i, tag in enumerate(tags):
        tag_display = tag.replace("_", " ").title()
        if cols[i % 2].checkbox(tag_display, value=True, key=f"tag_{tag}"):
            selected_tags.append(tag)

    if not selected_tags:
        st.warning("Selecciona al menos un tema.")
    else:
        filtered_count = len([q for q in questions if q["tag"] in selected_tags])
        st.info(f"Preguntas disponibles con los temas seleccionados: {filtered_count}")

        if st.button("Comenzar Test", type="primary"):
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
            st.session_state.questions = quiz_questions
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

        st.header("Ronda completada!")

        # Current round result
        percentage = (score / total) * 100
        st.subheader(f"Ronda {current_round}")
        st.metric("Puntuacion", f"{score}/{total} ({percentage:.1f}%)")

        if percentage >= 80:
            st.success("Excelente!")
        elif percentage >= 60:
            st.info("Buen trabajo!")
        else:
            st.warning("Sigue practicando!")

        # Accumulated summary across all rounds
        if len(history) > 1:
            st.divider()
            st.subheader("Resumen acumulado")
            total_all = sum(r["total"] for r in history)
            correct_all = sum(r["score"] for r in history)
            pct_all = (correct_all / total_all) * 100
            st.metric("Total acumulado", f"{correct_all}/{total_all} ({pct_all:.1f}%)")

            for r in history:
                r_pct = (r["score"] / r["total"]) * 100
                icon = "✓" if r_pct == 100 else "○"
                st.write(f"{icon} **Ronda {r['round']}:** {r['score']}/{r['total']} ({r_pct:.1f}%)")

        # Show wrong questions from current round
        if wrong:
            st.divider()
            st.subheader(f"Preguntas falladas en esta ronda ({len(wrong)})")
            for i, q in enumerate(wrong, 1):
                tag_display = q["tag"].replace("_", " ").title()
                with st.expander(f"{i}. {q['question']}"):
                    st.caption(f"Tema: {tag_display}")
                    correct = q["options"][q["answer_index"]]
                    st.success(f"Respuesta correcta: {correct}")
                    st.info(f"**Explicacion:** {q['explanation']}")

            col1, col2 = st.columns(2)
            with col1:
                if st.button("Repetir preguntas falladas", type="primary"):
                    next_round = current_round + 1
                    random.shuffle(wrong)
                    new_session_id = None
                    if _is_logged_in():
                        new_session_id = create_session(
                            st.session_state.user_id,
                            st.session_state.current_test_id,
                            0, len(wrong),
                        )
                    st.session_state.questions = wrong
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
                if st.button("Volver al inicio"):
                    reset_quiz()
                    st.rerun()
        else:
            if st.button("Volver al inicio"):
                reset_quiz()
                st.rerun()
        return

    question = questions[current_index]

    col1, col2 = st.columns([3, 1])
    with col1:
        st.progress((current_index) / len(questions))
    with col2:
        st.write(f"Pregunta {current_index + 1}/{len(questions)}")

    st.subheader(question["question"])

    tag_display = question["tag"].replace("_", " ").title()
    st.caption(f"Tema: {tag_display}")

    if not st.session_state.answered:
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
        selected = st.session_state.selected_answer

        for i, option in enumerate(question["options"]):
            if i == correct_index:
                st.success(f"✓ {option}")
            elif i == selected and selected != correct_index:
                st.error(f"✗ {option}")
            else:
                st.write(f"  {option}")

        if selected == correct_index:
            st.success("Correcto!")
        else:
            st.error("Incorrecto")

        st.info(f"**Explicacion:** {question['explanation']}")

        if st.button("Siguiente pregunta", type="primary"):
            st.session_state.current_index += 1
            st.session_state.answered = False
            st.session_state.selected_answer = None
            st.rerun()

    st.divider()
    if st.button("Abandonar test"):
        reset_quiz()
        st.rerun()


def show_dashboard():
    """Show the results dashboard."""
    st.header("Historial de resultados")

    user_id = st.session_state.user_id
    sessions = get_user_sessions(user_id)

    if not sessions:
        st.info("No hay resultados todavia. Completa un test para ver tu historial.")
        return

    # --- Sessions summary ---
    st.subheader("Sesiones anteriores")

    selected_session_ids = []

    for s in sessions:
        test_display = s["title"] or "Test desconocido"
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
                        # Group by test_id and load questions
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
                            st.caption(f"Tema: {tag_display}")
                            correct = q["options"][q["answer_index"]]
                            st.success(f"Respuesta correcta: {correct}")
                            st.info(f"**Explicacion:** {q['explanation']}")
                            st.write("---")
                    else:
                        st.write("No se encontraron detalles de preguntas falladas.")
            else:
                st.write(f"{label} ✓")
        with col2:
            if wrong_count > 0:
                if st.checkbox("Seleccionar", key=f"sel_session_{s['id']}", label_visibility="collapsed"):
                    selected_session_ids.append(s["id"])

    # --- Practice from selected sessions ---
    if selected_session_ids:
        st.divider()
        all_wrong = []
        for sid in selected_session_ids:
            wrong_refs = get_session_wrong_answers(sid)
            for w in wrong_refs:
                all_wrong.append(w)

        # Deduplicate by (test_id, question_id)
        seen = set()
        unique_wrong = []
        for w in all_wrong:
            key = (w["test_id"], w["question_id"])
            if key not in seen:
                seen.add(key)
                unique_wrong.append(w)

        st.write(f"**{len(unique_wrong)} preguntas falladas seleccionadas**")
        if st.button("Practicar preguntas falladas", type="primary"):
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
    st.session_state.questions = quiz_questions
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
    st.header("Crear nuevo test")

    if st.button("← Volver"):
        st.session_state.page = "Tests"
        st.rerun()

    title = st.text_input("Titulo del test", key="new_test_title")
    description = st.text_area("Descripcion", key="new_test_desc")
    author = st.text_input("Autor", key="new_test_author")

    if st.button("Crear test", type="primary"):
        if not title.strip():
            st.warning("El titulo es obligatorio.")
        else:
            test_id = create_test(st.session_state.user_id, title.strip(), description.strip(), author.strip())
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
        st.error("Test no encontrado.")
        return

    questions = get_test_questions(test_id)

    st.header(f"Editar: {test['title']}")

    if st.button("← Volver"):
        if "editing_test_id" in st.session_state:
            del st.session_state.editing_test_id
        st.session_state.page = "Tests"
        st.rerun()

    # --- Metadata ---
    st.subheader("Informacion del test")
    new_title = st.text_input("Titulo", value=test["title"], key="edit_title")
    new_desc = st.text_area("Descripcion", value=test["description"] or "", key="edit_desc")
    new_author = st.text_input("Autor", value=test["author"] or "", key="edit_author")

    if st.button("Guardar informacion", type="primary"):
        if not new_title.strip():
            st.warning("El titulo es obligatorio.")
        else:
            update_test(test_id, new_title.strip(), new_desc.strip(), new_author.strip())
            st.success("Informacion actualizada.")
            st.rerun()

    st.divider()

    # --- Questions ---
    st.subheader(f"Preguntas ({len(questions)})")

    if st.button("➕ Agregar pregunta"):
        next_num = get_next_question_num(test_id)
        add_question(test_id, next_num, "general", "Nueva pregunta", ["Opcion A", "Opcion B", "Opcion C", "Opcion D"], 0, "")
        st.rerun()

    for q in questions:
        with st.expander(f"#{q['id']} — {q['question'][:80]}"):
            q_key = f"q_{q['db_id']}"
            q_tag = st.text_input("Tema", value=q["tag"], key=f"{q_key}_tag")
            q_text = st.text_area("Pregunta", value=q["question"], key=f"{q_key}_text")
            q_explanation = st.text_area("Explicacion", value=q.get("explanation", ""), key=f"{q_key}_expl")

            st.write("**Opciones:**")
            options = []
            for oi in range(len(q["options"])):
                opt = st.text_input(f"Opcion {oi + 1}", value=q["options"][oi], key=f"{q_key}_opt_{oi}")
                options.append(opt)

            # Add/remove option buttons
            col_add, col_rm = st.columns(2)
            with col_add:
                if st.button("+ Opcion", key=f"{q_key}_add_opt"):
                    new_opts = q["options"] + [f"Opcion {len(q['options']) + 1}"]
                    update_question(q["db_id"], q["tag"], q["question"], new_opts, q["answer_index"], q.get("explanation", ""))
                    st.rerun()
            with col_rm:
                if len(q["options"]) > 2:
                    if st.button("- Opcion", key=f"{q_key}_rm_opt"):
                        new_opts = q["options"][:-1]
                        new_ans = min(q["answer_index"], len(new_opts) - 1)
                        update_question(q["db_id"], q["tag"], q["question"], new_opts, new_ans, q.get("explanation", ""))
                        st.rerun()

            q_answer = st.selectbox(
                "Respuesta correcta",
                range(len(options)),
                index=q["answer_index"],
                format_func=lambda i: options[i] if i < len(options) else "",
                key=f"{q_key}_ans",
            )

            col_save, col_del = st.columns(2)
            with col_save:
                if st.button("Guardar pregunta", key=f"{q_key}_save", type="primary"):
                    update_question(q["db_id"], q_tag.strip(), q_text.strip(), options, q_answer, q_explanation.strip())
                    st.success("Pregunta actualizada.")
                    st.rerun()
            with col_del:
                if st.button("Eliminar pregunta", key=f"{q_key}_del"):
                    delete_question(q["db_id"])
                    st.rerun()

    st.divider()

    # --- Delete test ---
    st.subheader("Zona peligrosa")
    if st.button("Eliminar test completo", type="secondary"):
        st.session_state[f"confirm_delete_{test_id}"] = True

    if st.session_state.get(f"confirm_delete_{test_id}"):
        st.warning("¿Estas seguro? Esta accion no se puede deshacer.")
        col_yes, col_no = st.columns(2)
        with col_yes:
            if st.button("Si, eliminar", type="primary"):
                delete_test(test_id)
                if "editing_test_id" in st.session_state:
                    del st.session_state.editing_test_id
                st.session_state.page = "Tests"
                st.rerun()
        with col_no:
            if st.button("Cancelar"):
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
    st.header("Perfil")

    profile = get_user_profile(st.session_state.user_id)
    current_name = profile["display_name"] or st.session_state.username
    current_avatar = profile["avatar"]

    # Show current avatar
    if current_avatar:
        st.image(current_avatar, width=120)
    else:
        st.markdown(_get_avatar_html(None, size=120), unsafe_allow_html=True)

    st.divider()

    display_name = st.text_input("Nombre para mostrar", value=current_name, key="profile_name_input")

    uploaded_file = st.file_uploader(
        "Subir foto de perfil",
        type=["png", "jpg", "jpeg"],
        key="profile_avatar_upload",
    )

    if st.button("Guardar", type="primary"):
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
        st.success("Perfil actualizado.")
        st.session_state.page = st.session_state.get("prev_page", "Tests")
        st.rerun()


def main():
    st.set_page_config(page_title="Mindoof", page_icon="📚")

    _try_login()

    if _is_logged_in():
        _load_profile_to_session()

    if "page" not in st.session_state:
        st.session_state.page = "Tests"

    logged_in = _is_logged_in()

    # Top bar: title + avatar/login
    col_title, col_avatar = st.columns([6, 1])
    with col_title:
        st.title("Mindoof")
        st.subheader("*No oof without proof*")
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
                if st.button("Perfil", key="menu_profile", use_container_width=True):
                    st.session_state.prev_page = st.session_state.page
                    st.session_state.page = "Perfil"
                    st.rerun()
                if st.button("Cerrar sesion", key="menu_logout", use_container_width=True):
                    for key in list(st.session_state.keys()):
                        del st.session_state[key]
                    st.logout()
                    st.rerun()
        else:
            st.button("Iniciar sesion", on_click=st.login, type="secondary")

    # Sidebar navigation
    with st.sidebar:
        st.markdown("---")
        nav_items = [("📝", "Tests")]
        if logged_in:
            nav_items.append(("📊", "Dashboard"))
        for icon, label in nav_items:
            current = st.session_state.page if st.session_state.page in [n[1] for n in nav_items] else "Tests"
            is_active = current == label
            btn_type = "primary" if is_active else "secondary"
            if st.button(f"{icon}  {label}", key=f"nav_{label}", use_container_width=True, type=btn_type):
                st.session_state.page = label
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
    elif st.session_state.quiz_started:
        show_quiz()
    else:
        show_test_catalog()


if __name__ == "__main__":
    main()
