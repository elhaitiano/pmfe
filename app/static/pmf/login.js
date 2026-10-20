function togglePasswordVisibility() {
  const input = document.getElementById('txtSenha');
  const visible = input.type === 'password';
  input.type = visible ? 'text' : 'password';
  document.getElementById('eyeIcon').textContent = visible ? 'visibility_off' : 'visibility';
}
function handleForgotPassword() {
  document.getElementById('msgStatus').textContent = 'Contacte o administrador local para repor a palavra-passe.';
}
