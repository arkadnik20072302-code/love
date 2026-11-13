const STORAGE_KEY = "love-theme";
const root = document.documentElement;
const themeButtons = [...document.querySelectorAll("[data-theme-toggle]")];

function setTheme(theme) {
  const selectedTheme = theme === "dark" ? "dark" : "light";
  root.dataset.theme = selectedTheme;
  document.querySelector('meta[name="theme-color"]')?.setAttribute(
    "content",
    selectedTheme === "dark" ? "#100d19" : "#f2f0e9",
  );
  try {
    localStorage.setItem(STORAGE_KEY, selectedTheme);
  } catch (error) {
    console.warn("Не удалось сохранить тему оформления.", error);
  }
  themeButtons.forEach(button => {
    const dark = selectedTheme === "dark";
    button.setAttribute("aria-label", dark ? "Включить светлую тему" : "Включить тёмную тему");
    button.setAttribute("title", dark ? "Светлая тема" : "Тёмная тема");
    button.setAttribute("aria-pressed", String(dark));
  });
}

let storedTheme = "light";
try {
  storedTheme = localStorage.getItem(STORAGE_KEY) || "light";
} catch (error) {
  console.warn("Не удалось прочитать сохранённую тему.", error);
}
setTheme(storedTheme);

themeButtons.forEach(button => button.addEventListener("click", () => {
  setTheme(root.dataset.theme === "dark" ? "light" : "dark");
}));
