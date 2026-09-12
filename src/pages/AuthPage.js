import { createElement } from '../utils/dom.js';
import api from '../services/api.js';
import store from '../services/store.js';
import router from '../services/router.js';

export default {
  render() {
    let isRegister = false;
    
    const page = createElement('div', { className: 'auth-page' });
    const card = createElement('div', { className: 'auth-card' });
    
    const logo = createElement('h1', { className: 'auth-logo' }, 'Overtone');
    const tagline = createElement('p', { className: 'auth-tagline' }, 'Find your vibe on campus');
    
    card.appendChild(logo);
    card.appendChild(tagline);
    
    const form = createElement('form', { className: 'auth-form' });
    
    const nameInput = createElement('input', { 
      type: 'text', 
      placeholder: 'Display Name', 
      className: 'form-input',
      style: 'display: none;'
    });
    const emailInput = createElement('input', { 
      type: 'email', 
      placeholder: 'Email', 
      className: 'form-input'
    });
    const passwordInput = createElement('input', { 
      type: 'password', 
      placeholder: 'Password', 
      className: 'form-input'
    });
    
    const errorMsg = createElement('div', { className: 'form-error' });
    errorMsg.style.display = 'none';
    
    const submitBtn = createElement('button', { 
      type: 'submit', 
      className: 'auth-submit'
    }, 'Log In');
    
    form.appendChild(nameInput);
    form.appendChild(emailInput);
    form.appendChild(passwordInput);
    form.appendChild(errorMsg);
    form.appendChild(submitBtn);
    
    const toggleLink = createElement('a', { className: 'auth-toggle', href: '#' }, "Don't have an account? Sign up");
    toggleLink.onclick = (e) => {
      e.preventDefault();
      isRegister = !isRegister;
      if (isRegister) {
        nameInput.style.display = 'block';
        submitBtn.textContent = 'Sign Up';
        toggleLink.textContent = 'Already have an account? Log in';
      } else {
        nameInput.style.display = 'none';
        submitBtn.textContent = 'Log In';
        toggleLink.textContent = "Don't have an account? Sign up";
      }
    };
    
    form.onsubmit = async (e) => {
      e.preventDefault();
      errorMsg.style.display = 'none';
      
      const email = emailInput.value.trim();
      const password = passwordInput.value.trim();
      const name = nameInput.value.trim();
      
      if (!email || !password || (isRegister && !name)) {
        errorMsg.textContent = 'Please fill in all required fields';
        errorMsg.style.display = 'block';
        return;
      }
      
      submitBtn.disabled = true;
      submitBtn.textContent = isRegister ? 'Signing up...' : 'Signing in...';
      
      try {
        let token;
        if (isRegister) {
          const res = await api.register(email, password, name);
          token = res.access_token;
          store.setState({ token: token });
          const user = await api.getMe();
          store.setState({ currentUser: user });
          router.navigate('/setup');
        } else {
          const res = await api.login(email, password);
          token = res.access_token;
          store.setState({ token: token });
          const user = await api.getMe();
          store.setState({ currentUser: user });
          router.navigate('/');
        }
      } catch (err) {
        errorMsg.textContent = err.message || 'Authentication failed';
        errorMsg.style.display = 'block';
        submitBtn.disabled = false;
        submitBtn.textContent = isRegister ? 'Sign Up' : 'Log In';
      }
    };
    
    card.appendChild(form);
    card.appendChild(toggleLink);
    page.appendChild(card);
    
    return page;
  }
};
