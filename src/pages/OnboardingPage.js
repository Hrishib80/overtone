import { createElement } from '../utils/dom.js';
import api from '../services/api.js';
import router from '../services/router.js';
import { toast } from '../utils/toast.js';
import { gateMessage, uploadFile } from '../services/upload.js';

/* Labels the machine name can't produce well on its own. Anything absent
   falls back to prettify(), which handles the ordinary cases. */
const LABELS = {
  life_partner: 'Life partner',
  long_term: 'Long-term relationship',
  long_term_open_to_short: 'Long-term, open to short',
  short_term_open_to_long: 'Short-term, open to long',
  short_term: 'Short-term fun',
  figuring_it_out: 'Figuring it out',
  non_monogamy: 'Non-monogamy',
  prefer_not_to_say: 'Prefer not to say',
  dont_want_children: "Don't want children",
  want_children: 'Want children',
  open_to_children: 'Open to children',
  not_sure_yet: 'Not sure yet',
  dont_have_children: "Don't have children",
  have_children: 'Have children',
  parsi_zoroastrian: 'Parsi / Zoroastrian',
  black_african_descent: 'Black / African descent',
  hispanic_latino: 'Hispanic / Latino',
  white_caucasian: 'White / Caucasian',
  south_asian: 'South Asian',
  east_asian: 'East Asian',
  southeast_asian: 'Southeast Asian',
  central_asian: 'Central Asian',
  middle_eastern: 'Middle Eastern',
  native_american: 'Native American',
  pacific_islander: 'Pacific Islander',
  not_political: 'Not political',
  high_school: 'High school',
  undergrad: 'Undergraduate',
  postgrad: 'Postgraduate',
  man: 'Men',
  woman: 'Women',
  nonbinary: 'Non-binary people',
};

const prettify = (value) =>
  value.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());

const label = (value) => LABELS[value] || prettify(value);

function selectField(id, labelText, values, { placeholder = 'Select…', hint } = {}) {
  const select = createElement('select', { className: 'select', id });
  select.append(createElement('option', { value: '' }, placeholder));
  for (const value of values) {
    select.append(createElement('option', { value }, label(value)));
  }
  const wrap = createElement('div', { className: 'field' });
  wrap.append(createElement('label', { for: id }, labelText), select);
  if (hint) wrap.append(createElement('p', { className: 'hint' }, hint));
  return { wrap, select };
}

function chipGroup(labelText, values, { blue = false, hint } = {}) {
  const selected = new Set();
  const wrap = createElement('div', { className: 'field' });
  wrap.append(createElement('span', { className: 'field-label' }, labelText));
  wrap.firstChild.className = '';
  wrap.firstChild.style.cssText = 'font-weight:600;font-size:var(--t--1)';

  const group = createElement('div', { className: 'chips', role: 'group' });
  for (const value of values) {
    const chip = createElement(
      'button',
      {
        type: 'button',
        className: blue ? 'chip chip--blue' : 'chip',
        'aria-pressed': 'false',
      },
      label(value)
    );
    chip.addEventListener('click', () => {
      const on = selected.has(value);
      if (on) selected.delete(value);
      else selected.add(value);
      chip.setAttribute('aria-pressed', String(!on));
    });
    group.append(chip);
  }
  wrap.append(group);
  if (hint) wrap.append(createElement('p', { className: 'hint' }, hint));

  return { wrap, values: () => [...selected] };
}

export default {
  async render() {
    const page = createElement('div', { className: 'ob' });

    let options;
    try {
      options = await api.getOptions();
    } catch (error) {
      toast(error.message, { error: true });
      options = { gender_identities: [], sexualities: [], prompts: [], fields: {} };
    }
    const F = options.fields || {};

    // ---- header ----------------------------------------------------------
    const head = createElement('header', { className: 'ob__head' });
    const headInner = createElement('div', { className: 'ob__head-inner' });
    const stepLabel = createElement('span', { className: 'ob__step' }, 'Step 1 of 4');
    const progress = createElement('div', { className: 'progress' });
    const bar = createElement('div', { className: 'progress__bar' });
    progress.append(bar);
    headInner.append(stepLabel, progress);
    head.append(headInner);

    const body = createElement('main', { className: 'ob__body' });

    // ---- step 1: identity ------------------------------------------------
    const step1 = createElement('section', { className: 'stack' });
    step1.append(
      createElement('h2', {}, 'Who you are'),
      createElement(
        'p',
        { className: 'ob__lede' },
        'Your identity is shown exactly as you write it. What you appear in, and what you see, are separate choices below.'
      )
    );

    const gender = selectField(
      'gender',
      'Gender identity',
      options.gender_identities.map((g) => g.id),
      { placeholder: 'Select your gender' }
    );
    // Reuse the server's labels rather than prettifying ids.
    [...gender.select.options].forEach((opt) => {
      const match = options.gender_identities.find((g) => g.id === opt.value);
      if (match) opt.textContent = match.label;
    });

    const pronouns = createElement('input', {
      className: 'input',
      id: 'pronouns',
      type: 'text',
      placeholder: 'she/her, he/him, they/them…',
      maxlength: '40',
    });
    const pronounsField = createElement('div', { className: 'field' });
    pronounsField.append(createElement('label', { for: 'pronouns' }, 'Pronouns'), pronouns);

    const visibleAs = chipGroup('Show me in searches for', F.segments || [], {
      hint: 'Who should be able to come across you.',
    });
    const interestedIn = chipGroup('I want to see', F.segments || [], {
      blue: true,
      hint: 'Whose profiles you want to be shown.',
    });

    step1.append(gender.wrap, pronounsField, visibleAs.wrap, interestedIn.wrap);

    // ---- step 2: about you ----------------------------------------------
    const step2 = createElement('section', { className: 'stack', hidden: 'true' });
    step2.append(
      createElement('h2', {}, 'The basics'),
      createElement('p', { className: 'ob__lede' }, 'Only dating intentions is required. The rest you can fill in later.')
    );

    const intentions = selectField('intentions', 'Dating intentions', F.dating_intentions || []);
    const relType = selectField('reltype', 'Relationship type', F.relationship_types || []);
    const height = createElement('input', {
      className: 'input',
      id: 'height',
      type: 'number',
      min: '120',
      max: '230',
      inputmode: 'numeric',
      placeholder: '170',
    });
    const heightField = createElement('div', { className: 'field' });
    heightField.append(createElement('label', { for: 'height' }, 'Height (cm)'), height);

    const school = createElement('input', {
      className: 'input',
      id: 'school',
      type: 'text',
      placeholder: 'Department or college',
      maxlength: '120',
    });
    const schoolField = createElement('div', { className: 'field' });
    schoolField.append(createElement('label', { for: 'school' }, 'College'), school);

    const languages = chipGroup('Languages you speak', (F.languages || []).slice(0, 8), { blue: true });
    const religion = selectField('religion', 'Religious beliefs', F.religions || []);
    const drinking = selectField('drinking', 'Drinking', F.frequency || []);
    const smoking = selectField('smoking', 'Smoking', F.frequency || []);

    const pair = createElement('div', { className: 'grid-2' });
    pair.append(heightField, schoolField);
    const vices = createElement('div', { className: 'grid-2' });
    vices.append(drinking.wrap, smoking.wrap);

    step2.append(intentions.wrap, relType.wrap, pair, languages.wrap, religion.wrap, vices);

    // ---- step 3: photos --------------------------------------------------
    /* Server state, not local state. The old version appended a tile per file
       and never read anything back, so the grid disagreed with reality the
       moment anything failed, and coming back to this step showed nothing you
       had already uploaded. */
    const MAX_PHOTOS = 3;

    const stepPhotos = createElement('section', { className: 'stack', hidden: 'true' });
    stepPhotos.append(
      createElement('h2', {}, 'Your photos'),
      createElement(
        'p',
        { className: 'ob__lede' },
        'One clear photo of your face on its own, and up to two more if you want. The first is the only one people see while choosing, so it stays the same every time.'
      )
    );

    const photoGrid = createElement('div', { className: 'photo-grid' });
    const photoInput = createElement('input', {
      type: 'file',
      accept: 'image/jpeg,image/png,image/webp',
      className: 'visually-hidden',
      id: 'photo-input',
      multiple: 'true',
    });
    const photoNote = createElement('p', { className: 'photo-note' });
    stepPhotos.append(photoGrid, photoNote, photoInput);

    let photos = [];
    // Set while a replacement is in flight, so the file picker knows whether
    // the next file is a new photo or a swap for an existing one.
    let replacing = null;

    async function loadPhotos() {
      try {
        const { media } = await api.request('GET', '/api/media');
        photos = media
          .filter((m) => m.kind === 'photo' && m.status !== 'rejected')
          .sort((a, b) => Number(b.is_primary) - Number(a.is_primary));
      } catch {
        photos = [];
      }
      renderPhotos();
    }

    function tileFor(photo, index) {
      const tile = createElement('div', { className: 'photo-tile photo-tile--ok' });
      if (photo.url) {
        tile.append(createElement('img', { src: photo.url, alt: '' }));
      }

      if (index === 0) {
        tile.append(createElement('span', { className: 'photo-tile__badge' }, 'Shown in pairs'));
      }

      const actions = createElement('div', { className: 'photo-tile__actions' });

      const replace = createElement(
        'button',
        { className: 'photo-tile__action', type: 'button' },
        'Replace'
      );
      replace.addEventListener('click', () => {
        replacing = photo.id;
        photoInput.removeAttribute('multiple');
        photoInput.click();
      });
      actions.append(replace);

      // The first photo is what the pair view shows. It can be swapped, but
      // removing it would leave nothing to be compared on, so it has no
      // Remove — the server refuses it too, and this is the half that stops
      // somebody being told off for trying.
      if (index > 0) {
        const remove = createElement(
          'button',
          { className: 'photo-tile__action photo-tile__action--remove', type: 'button' },
          'Remove'
        );
        remove.addEventListener('click', async () => {
          remove.disabled = true;
          try {
            await api.request('DELETE', `/api/media/${photo.id}`);
          } catch (error) {
            toast(error.message, { error: true });
          }
          await loadPhotos();
        });
        actions.append(remove);
      }

      tile.append(actions);
      return tile;
    }

    function renderPhotos() {
      photoGrid.replaceChildren(...photos.map(tileFor));

      if (photos.length < MAX_PHOTOS) {
        const addTile = createElement('button', { className: 'photo-add', type: 'button' });
        addTile.append(
          createElement('span', { className: 'photo-add__plus' }, '+'),
          createElement(
            'span',
            { className: 'photo-add__label' },
            photos.length === 0 ? 'Add a photo' : 'Add another'
          )
        );
        addTile.addEventListener('click', () => {
          replacing = null;
          photoInput.setAttribute('multiple', 'true');
          photoInput.click();
        });
        photoGrid.append(addTile);
      }

      photoNote.textContent =
        photos.length === 0
          ? 'At least one photo is needed.'
          : `${photos.length} of ${MAX_PHOTOS}. The first one is the one people compare.`;
    }

    photoInput.addEventListener('change', async () => {
      const files = [...photoInput.files];
      photoInput.value = '';
      const swapping = replacing;
      replacing = null;

      // Never start more uploads than there are slots; the server refuses the
      // extras anyway and each refusal is a toast the person did not need.
      const room = swapping ? 1 : Math.max(0, MAX_PHOTOS - photos.length);
      const chosen = files.slice(0, room);
      if (files.length > chosen.length) {
        toast(`You can have ${MAX_PHOTOS} photos.`);
      }

      for (const file of chosen) {
        const pending = createElement('div', { className: 'photo-tile photo-tile--busy' });
        pending.append(createElement('img', { src: URL.createObjectURL(file), alt: '' }));
        const note = createElement('span', { className: 'photo-tile__note' }, 'Uploading…');
        pending.append(note);
        photoGrid.append(pending);

        try {
          await uploadFile(file, {
            kind: 'photo',
            replaces: swapping,
            onProgress: (phase) => {
              if (phase === 'confirming') note.textContent = 'Checking…';
            },
          });
        } catch (error) {
          toast(error.message, { error: true });
        }
        pending.remove();
        await loadPhotos();
      }
    });

    // The face gate runs in the worker, so its verdict lands after the upload
    // returns. Check before moving on, otherwise a rejected photo is accepted
    // here and quietly missing later.
    async function photosStillValid() {
      try {
        const { media } = await api.request('GET', '/api/media');
        const all = media.filter((m) => m.kind === 'photo');
        for (const photo of all.filter((m) => m.status === 'rejected')) {
          toast(gateMessage(photo.gate_reason), { error: true });
        }
        const usable = all.filter((m) => m.status !== 'rejected');
        if (!usable.length) {
          toast('Add at least one photo to carry on.', { error: true });
          return false;
        }
        return true;
      } catch {
        return true;
      }
    }

    // ---- step 4: prompts -------------------------------------------------
    const step3 = createElement('section', { className: 'stack', hidden: 'true' });
    step3.append(
      createElement('h2', {}, 'Say something'),
      createElement(
        'p',
        { className: 'ob__lede' },
        'Three written answers and one in your own voice. This is what people actually read.'
      )
    );

    const written = [];
    for (let slot = 1; slot <= 3; slot += 1) {
      const card = createElement('div', { className: 'prompt-slot' });
      card.append(createElement('div', { className: 'prompt-slot__label' }, `Written prompt ${slot}`));

      const select = createElement('select', { className: 'select', id: `prompt-${slot}` });
      select.append(createElement('option', { value: '' }, 'Choose a prompt…'));
      for (const prompt of options.prompts) {
        select.append(createElement('option', { value: prompt.id }, prompt.text));
      }

      const answer = createElement('textarea', {
        className: 'textarea',
        id: `answer-${slot}`,
        maxlength: '500',
        placeholder: 'Your answer',
      });
      const counter = createElement('div', { className: 'counter' }, '0 / 500');
      answer.addEventListener('input', () => {
        counter.textContent = `${answer.value.length} / 500`;
      });

      card.append(select, answer, counter);
      step3.append(card);
      written.push({ select, answer });
    }

    const voiceCard = createElement('div', { className: 'prompt-slot prompt-slot--voice' });
    voiceCard.append(createElement('div', { className: 'prompt-slot__label' }, 'Voice prompt · 15s max'));

    const voiceSelect = createElement('select', { className: 'select', id: 'prompt-voice' });
    voiceSelect.append(createElement('option', { value: '' }, 'Choose a prompt…'));
    for (const prompt of options.prompts) {
      voiceSelect.append(createElement('option', { value: prompt.id }, prompt.text));
    }

    const recorder = createElement('div', { className: 'recorder' });
    const recBtn = createElement('button', {
      className: 'rec-btn',
      type: 'button',
      'data-recording': 'false',
      'aria-label': 'Record your voice answer',
    }, '●');
    const recStatus = createElement('div', { className: 'rec-status' }, 'Tap to record');
    recorder.append(recBtn, recStatus);
    voiceCard.append(voiceSelect, recorder);
    step3.append(voiceCard);

    // Recording state. The clip itself is uploaded in phase 02 — for now the
    // duration is what the API needs, and the key is a placeholder.
    let recorder_ = null;
    let chunks = [];
    let durationMs = 0;
    let voiceBlob = null;
    let timer = null;

    recBtn.addEventListener('click', async () => {
      if (recorder_ && recorder_.state === 'recording') {
        recorder_.stop();
        return;
      }
      if (durationMs) {
        durationMs = 0;
        voiceBlob = null;
        recStatus.textContent = 'Tap to record';
        recBtn.textContent = '●';
        return;
      }
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        recorder_ = new MediaRecorder(stream);
        chunks = [];
        const started = Date.now();

        recorder_.ondataavailable = (e) => e.data.size && chunks.push(e.data);
        recorder_.onstop = () => {
          clearInterval(timer);
          stream.getTracks().forEach((t) => t.stop());
          durationMs = Math.min(Date.now() - started, 15000);
          recBtn.dataset.recording = 'false';
          recBtn.textContent = '×';

          const blob = new Blob(chunks, { type: 'audio/webm' });
          voiceBlob = blob;
          recStatus.replaceChildren(
            createElement('span', {}, `Recorded ${(durationMs / 1000).toFixed(1)}s — tap to clear`),
            createElement('audio', { controls: 'true', src: URL.createObjectURL(blob) })
          );
        };

        recorder_.start();
        recBtn.dataset.recording = 'true';
        recBtn.textContent = '■';
        recStatus.textContent = 'Recording… 0.0s';
        timer = setInterval(() => {
          const elapsed = (Date.now() - started) / 1000;
          recStatus.textContent = `Recording… ${elapsed.toFixed(1)}s`;
          if (elapsed >= 15) recorder_.stop();
        }, 100);
      } catch {
        toast('Microphone permission is needed for the voice prompt.', { error: true });
      }
    });

    body.append(step1, step2, stepPhotos, step3);

    // ---- footer / navigation --------------------------------------------
    const foot = createElement('footer', { className: 'ob__foot' });
    const footInner = createElement('div', { className: 'ob__foot-inner' });
    const backBtn = createElement('button', { className: 'btn btn--ghost', type: 'button', hidden: 'true' }, 'Back');
    const nextBtn = createElement('button', { className: 'btn', type: 'button' }, 'Continue');
    footInner.append(backBtn, nextBtn);
    foot.append(footInner);

    const steps = [step1, step2, stepPhotos, step3];
    let index = 0;

    function show(next) {
      // Which way the funnel is moving, so the incoming step enters from the
      // side it came from. A step that always slides the same way makes Back
      // feel like another Continue.
      const backwards = next < index;
      index = next;

      steps.forEach((el, i) => {
        el.hidden = i !== index;
      });

      const current = steps[index];
      current.classList.remove('step-in', 'step-in--back');
      void current.offsetWidth; // restart rather than skip
      current.classList.add(backwards ? 'step-in--back' : 'step-in');

      if (steps[index] === stepPhotos) loadPhotos();

      stepLabel.textContent = `Step ${index + 1} of ${steps.length}`;
      bar.style.width = `${((index + 1) / steps.length) * 100}%`;
      backBtn.hidden = index === 0;
      nextBtn.textContent = index === steps.length - 1 ? 'Finish' : 'Continue';
      body.scrollTo?.({ top: 0 });
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    show(0);

    backBtn.addEventListener('click', () => show(index - 1));

    nextBtn.addEventListener('click', async () => {
      nextBtn.disabled = true;
      try {
        if (index === 0) {
          if (!visibleAs.values().length || !interestedIn.values().length) {
            toast('Pick at least one option in each of the last two questions.', { error: true });
            return;
          }
          const changes = {
            visible_as: visibleAs.values(),
            interested_in: interestedIn.values(),
          };
          if (gender.select.value) changes.gender_identity_id = gender.select.value;
          if (pronouns.value.trim()) changes.pronouns = pronouns.value.trim();
          await api.patchProfile(changes);
          show(1);
          return;
        }

        if (index === 1) {
          if (!intentions.select.value) {
            toast('Dating intentions is required.', { error: true });
            return;
          }
          const changes = { dating_intentions: intentions.select.value };
          if (relType.select.value) changes.relationship_type = relType.select.value;
          if (height.value) changes.height_cm = Number(height.value);
          if (school.value.trim()) changes.school = school.value.trim();
          if (languages.values().length) changes.languages = languages.values();
          if (religion.select.value) changes.religion = religion.select.value;
          if (drinking.select.value) changes.drinking = drinking.select.value;
          if (smoking.select.value) changes.smoking = smoking.select.value;
          await api.patchProfile(changes);
          show(2);
          return;
        }

        if (index === 2) {
          if (!uploaded.length) {
            toast('Add at least one photo to carry on.', { error: true });
            return;
          }
          if (!(await photosStillValid())) {
            toast('None of those passed. Try a clear photo of just your face.', { error: true });
            return;
          }
          show(3);
          return;
        }

        const answers = [];
        written.forEach(({ select, answer }, i) => {
          if (select.value && answer.value.trim()) {
            answers.push({
              prompt_id: select.value,
              slot: i + 1,
              kind: 'written',
              body: answer.value.trim(),
            });
          }
        });
        if (answers.length < 3) {
          toast('Answer all three written prompts.', { error: true });
          return;
        }
        if (!voiceSelect.value || !durationMs) {
          toast('Record your voice prompt to finish.', { error: true });
          return;
        }
        nextBtn.textContent = 'Uploading...';
        const voiceUpload = await uploadFile(
          new File([voiceBlob], 'voice.webm', { type: 'audio/webm' }),
          { kind: 'voice', durationMs }
        );

        answers.push({
          prompt_id: voiceSelect.value,
          slot: 4,
          kind: 'voice',
          audio_key: voiceUpload.assetId,
          audio_duration_ms: durationMs,
        });

        await api.setPrompts(answers);
        await api.submitProfile();
        await router.refresh(await api.getMe());
      } catch (error) {
        toast(error.message, { error: true });
      } finally {
        nextBtn.disabled = false;
      }
    });

    page.append(head, body, foot);
    return page;
  },
};
