import streamlit as st

st.title("Hello 👋")
name = st.text_input("Your name")
if name:
    st.success(f"Hello, {name}!")

