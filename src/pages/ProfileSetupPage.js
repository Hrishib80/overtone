import { createElement } from '../utils/dom.js';
import api from '../services/api.js';
import store from '../services/store.js';
import router from '../services/router.js';

const AVAILABLE_PROMPTS = [
  "A shower thought I recently had...",
  "My most controversial opinion is...",
  "I'm looking for...",
  "My simple pleasures...",
  "The most spontaneous thing I've done...",
  "Two truths and a lie...",
  "A random fact I love is..."
];

export default {
  render() {
    const page = createElement('div', { className: 'setup-page' });
    
    const header = createElement('header', { className: 'setup-header' });
    const title = createElement('h1', {}, 'Set up your profile');
    const saveBtn = createElement('button', { className: 'btn btn--primary' }, 'Save & Continue');
    header.appendChild(title);
    header.appendChild(saveBtn);
    page.appendChild(header);
    
    const content = createElement('div', { className: 'setup-content' });
    
    // Photos & Video Section (6 slots)
    const mediaSection = createElement('section', { className: 'setup-section' });
    mediaSection.innerHTML = `
      <h2>Photos & Video</h2>
      <p>Upload up to 6 photos or a short video clip (minimum 2 items).</p>
    `;
    const mediaGrid = createElement('div', { className: 'media-grid' });
    const mediaFiles = new Array(6).fill(null);
    
    for (let i = 0; i < 6; i++) {
      const slot = createElement('div', { className: 'media-slot' });
      const plusIcon = createElement('span', { className: 'plus-icon' }, '+');
      const input = createElement('input', { type: 'file', className: 'hidden-file-input', accept: 'image/*,video/*' });
      
      slot.appendChild(plusIcon);
      slot.appendChild(input);
      
      slot.onclick = () => input.click();
      input.onchange = (e) => {
        const file = e.target.files[0];
        if (file) {
          mediaFiles[i] = file;
          slot.classList.add('has-media');
          
          const url = URL.createObjectURL(file);
          if (file.type.startsWith('video/')) {
            const vid = createElement('video', { src: url, autoplay: true, muted: true, loop: true });
            slot.appendChild(vid);
          } else {
            const img = createElement('img', { src: url });
            slot.appendChild(img);
          }
        }
      };
      mediaGrid.appendChild(slot);
    }
    mediaSection.appendChild(mediaGrid);
    content.appendChild(mediaSection);
    
    // Audio Intro Section
    const audioSection = createElement('section', { className: 'setup-section' });
    audioSection.innerHTML = `
      <h2>Audio Intro</h2>
      <p>Record a short voice prompt so people can hear your vibe.</p>
    `;
    const recorderDiv = createElement('div', { className: 'audio-recorder' });
    const recordBtn = createElement('button', { className: 'record-btn' }, '🎙');
    const statusText = createElement('div', { className: 'record-status' }, 'Tap to record');
    recorderDiv.appendChild(recordBtn);
    recorderDiv.appendChild(statusText);
    audioSection.appendChild(recorderDiv);
    content.appendChild(audioSection);
    
    let mediaRecorder;
    let audioChunks = [];
    let audioFile = null;
    let isRecording = false;

    recordBtn.onclick = async () => {
      if (audioFile) return; // already recorded

      if (!isRecording) {
        try {
          const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
          mediaRecorder = new MediaRecorder(stream);
          audioChunks = [];
          
          mediaRecorder.ondataavailable = e => {
            if (e.data.size > 0) audioChunks.push(e.data);
          };
          
          mediaRecorder.onstop = () => {
            const blob = new Blob(audioChunks, { type: 'audio/webm' });
            audioFile = new File([blob], 'intro.webm', { type: 'audio/webm' });
            
            statusText.innerHTML = '';
            const audioEl = createElement('audio', { src: URL.createObjectURL(blob), controls: true, className: 'audio-playback' });
            const removeBtn = createElement('button', { className: 'audio-remove' }, 'Remove');
            removeBtn.onclick = () => {
              audioFile = null;
              statusText.innerHTML = 'Tap to record';
              recordBtn.style.display = 'flex';
            };
            statusText.appendChild(audioEl);
            statusText.appendChild(removeBtn);
            recordBtn.style.display = 'none';
          };
          
          mediaRecorder.start();
          isRecording = true;
          recordBtn.classList.add('recording');
          recordBtn.textContent = '⏹';
          statusText.textContent = 'Recording... tap to stop';
        } catch (err) {
          alert('Microphone access denied or unavailable.');
        }
      } else {
        mediaRecorder.stop();
        mediaRecorder.stream.getTracks().forEach(t => t.stop());
        isRecording = false;
        recordBtn.classList.remove('recording');
        recordBtn.textContent = '🎙';
      }
    };
    
    // Prompts Section (3 items)
    const promptsSection = createElement('section', { className: 'setup-section' });
    promptsSection.innerHTML = `
      <h2>Profile Prompts</h2>
      <p>Answer 3 prompts to show your personality.</p>
    `;
    const promptsList = createElement('div', { className: 'prompts-list' });
    const promptData = [];
    
    for (let i = 0; i < 3; i++) {
      const pItem = createElement('div', { className: 'prompt-item' });
      const select = createElement('select', { className: 'prompt-selector' });
      select.innerHTML = '<option value="">Select a prompt...</option>' + 
                         AVAILABLE_PROMPTS.map(p => `<option value="${p}">${p}</option>`).join('');
      const textarea = createElement('textarea', { className: 'prompt-answer', placeholder: 'Write your answer here...' });
      
      pItem.appendChild(select);
      pItem.appendChild(textarea);
      promptsList.appendChild(pItem);
      promptData.push({ select, textarea });
    }
    promptsSection.appendChild(promptsList);
    content.appendChild(promptsSection);
    
    page.appendChild(content);
    
    saveBtn.onclick = async () => {
      saveBtn.disabled = true;
      saveBtn.textContent = 'Saving...';
      
      try {
        let currentUser = store.getState().currentUser;
        if (!currentUser) {
          currentUser = await api.getMe();
          store.setState({ currentUser });
        }
        
        // 1. Validate
        const finalMediaFiles = mediaFiles.filter(f => f !== null);
        if (finalMediaFiles.length < 2) throw new Error("Please upload at least 2 photos/videos.");
        
        const finalPrompts = promptData
          .filter(p => p.select.value && p.textarea.value.trim())
          .map(p => ({ question: p.select.value, answer: p.textarea.value.trim() }));
          
        if (finalPrompts.length < 3) throw new Error("Please answer all 3 prompts.");
        
        // 2. Upload media
        const uploadedMedia = [];
        
        for (const file of finalMediaFiles) {
          const res = await api.uploadMedia(file);
          uploadedMedia.push({
            asset_type: file.type.startsWith('video/') ? 'video' : 'photo',
            local_file_path: res.local_file_path,
            public_url: res.public_url
          });
        }
        
        if (audioFile) {
          const res = await api.uploadMedia(audioFile);
          uploadedMedia.push({
            asset_type: 'audio',
            local_file_path: res.local_file_path,
            public_url: res.public_url
          });
        }
        
        // 3. Ingest Profile
        await api.ingestProfile({
          user_id: currentUser.id,
          display_name: currentUser.display_name || currentUser.email.split('@')[0],
          prompts: finalPrompts,
          media: uploadedMedia
        });
        
        // Reload user to get avatar
        const updatedUser = await api.getMe();
        store.setState({ currentUser: updatedUser });
        
        router.navigate('/');
      } catch (err) {
        alert(err.message || "Failed to save profile");
        saveBtn.disabled = false;
        saveBtn.textContent = 'Save & Continue';
      }
    };
    
    return page;
  }
};
