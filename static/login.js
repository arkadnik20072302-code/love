const form = document.querySelector("#login-form");
const password = document.querySelector("#password");
const errorMessage = document.querySelector("#login-error");
const submitButton = form.querySelector("button");

form.addEventListener("submit", async event => {
  event.preventDefault();
  errorMessage.textContent = "";
  submitButton.disabled = true;
  try {
    const response = await fetch("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: password.value }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Не удалось войти.");
    location.replace("/");
  } catch (error) {
    errorMessage.textContent = error.message || "Не удалось войти. Попробуйте ещё раз.";
    password.select();
  } finally {
    submitButton.disabled = false;
  }
});
