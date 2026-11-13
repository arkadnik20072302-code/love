import { currentUser, isWishlistMember, signIn, signOut } from "./pages-client.js?v=memory-stories";

const form = document.querySelector("#login-form");
const password = document.querySelector("#password");
const errorMessage = document.querySelector("#login-error");
const submitButton = form.querySelector("button");
const passwordLabel = form.querySelector('label[for="password"]');
const emailLabel = document.createElement("label");
const emailInput = document.createElement("input");

emailLabel.htmlFor = "email";
emailLabel.textContent = "Электронная почта";
emailInput.id = "email";
emailInput.name = "email";
emailInput.type = "email";
emailInput.autocomplete = "username";
emailInput.required = true;
emailInput.maxLength = 254;
passwordLabel.before(emailInput);
emailInput.before(emailLabel);
password.autocomplete = "current-password";
document.querySelector(".login-copy").textContent =
  "Войдите по личному аккаунту Supabase, который добавлен в список доступа.";

try {
  const user = await currentUser();
  if (user && await isWishlistMember()) {
    location.replace("./");
  } else if (user) {
    await signOut();
    errorMessage.textContent = "Этот аккаунт пока не добавлен в список доступа.";
  }
} catch (error) {
  errorMessage.textContent = error.message;
}

form.addEventListener("submit", async event => {
  event.preventDefault();
  errorMessage.textContent = "";
  submitButton.disabled = true;
  try {
    await signIn(emailInput.value.trim(), password.value);
    if (!await isWishlistMember()) {
      await signOut();
      throw new Error("Этот аккаунт пока не добавлен в список доступа.");
    }
    location.replace("./");
  } catch (error) {
    errorMessage.textContent = error.message || "Не удалось войти. Попробуйте ещё раз.";
    password.select();
  } finally {
    submitButton.disabled = false;
  }
});
