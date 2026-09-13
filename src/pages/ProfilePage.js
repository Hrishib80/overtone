import { createElement } from '../utils/dom.js';
import { navbar, reviewerLink } from '../components/navbar.js';
import { gateMessage, uploadFile } from '../services/upload.js';
import api from '../services/api.js';
import { toast } from '../utils/toast.js';

/* Editing how you appear, after onboarding.

   Until now the only way to set any of this was the onboarding funnel, which
   you go through once and can never return to — so a typo in a prompt answer,
   or a photo you went off, was permanent. That is a strange thing for a
   dating app to be strict about.

   It is the same three things onboarding collects, without the funnel: who
   you are, your photos, and what you wrote. Each section saves on its own
   rather than the page having one Save at the bottom, because these are three
   unrelated edits and failing all of them because one select is empty is the
   behaviour of a form, not of a profile.

   The photo rules are the server's and are not restated as validation here —
   three slots, at least one, the first can be swapped but not removed. The UI
   only has to not offer what the server will refuse. */

const MAX_PHOTOS = 3;

export default {
  async render() {
    const nav = navbar('/profile');
    reviewerLink(nav);

    const page = createElement('div', { className: 'people' });
    const head = createElement('header', { className: 'people__head' });
    head.append(
      createElement('h1', { className: 'people__title' }, 'Your profile'),
      createElement('p', { className: 'people__lede' }, 'What other people see when a pair opens up.')
    );
    const body = createElement('main', { className: 'people__body people__body--stack' });
    page.append(nav, head, body);

    let options = { gender_identities: [], sexualities: [], prompts: [], fields: {} };
    let profile = null;

    // ---- photos -----------------------------------------------------------

    const photoCard = section('Photos', `Up to ${MAX_PHOTOS}. The first is the one people compare.`);
    const photoGrid = createElement('div', { className: 'edit__photos' });
    const photoInput = createElement('input', {
      type: 'file',
      accept: 'image/jpeg,image/png,image/webp',
      className: 'visually-hidden',
      id: 'edit-photo',
    });
    const photoNote = createElement('p', { className: 'people__muted' });
    photoCard.append(photoGrid, photoNote, photoInput);

    let photos = [];
    let replacing = null;

    async function loadPhotos() {
      try {
        const { media } = await api.request('GET', '/api/media');
        photos = media
          .filter((m) => m.kind === 'photo' && m.status !== 'rejected')
          .sort((a, b) => Number(b.is_primary) - Number(a.is_primary));
        for (const rejected of media.filter((m) => m.kind === 'photo' && m.status === 'rejected')) {
          if (rejected.gate_reason) toast(gateMessage(rejected.gate_reason), { error: true });
        }
      } catch {
        photos = [];
      }
      renderPhotos();
    }

    function renderPhotos() {
      photoGrid.replaceChildren(
        ...photos.map((photo, index) => {
          const tile = createElement('div', { className: 'edit__photo' });
          if (photo.url) tile.append(createElement('img', { src: photo.url, alt: '' }));

          if (photo.status === 'held') {
            tile.append(createElement('span', { className: 'edit__flag' }, 'Being checked'));
          } else if (index === 0) {
            tile.append(createElement('span', { className: 'edit__flag' }, 'Shown in pairs'));
          }

          const actions = createElement('div', { className: 'edit__photo-actions' });
          const swap = createElement('button', { className: 'btn btn--ghost btn--small', type: 'button' }, 'Replace');
          swap.addEventListener('click', () => {
            replacing = photo.id;
            photoInput.click();
          });
          actions.append(swap);

          // The first photo is what the pair view shows. Removing it would
          // leave nothing to be compared on, so it is swap-only — and the
          // server refuses it too.
          if (index > 0) {
            const drop = createElement('button', { className: 'btn btn--ghost btn--small', type: 'button' }, 'Remove');
            drop.addEventListener('click', async () => {
              drop.disabled = true;
              try {
                await api.request('DELETE', `/api/media/${photo.id}`);
              } catch (error) {
                toast(error.message, { error: true });
              }
              await loadPhotos();
            });
            actions.append(drop);
          }

          tile.append(actions);
          return tile;
        })
      );

      if (photos.length < MAX_PHOTOS) {
        const add = createElement('button', { className: 'edit__add', type: 'button' });
        add.append(
          createElement('span', { className: 'edit__add-plus' }, '+'),
          createElement('span', {}, photos.length ? 'Add another' : 'Add a photo')
        );
        add.addEventListener('click', () => {
          replacing = null;
          photoInput.click();
        });
        photoGrid.append(add);
      }

      photoNote.textContent = `${photos.length} of ${MAX_PHOTOS}.`;
    }

    photoInput.addEventListener('change', async () => {
      const [file] = photoInput.files;
      photoInput.value = '';
      const swapping = replacing;
      replacing = null;
      if (!file) return;

      photoNote.textContent = 'Uploading…';
      try {
        await uploadFile(file, { kind: 'photo', replaces: swapping });
      } catch (error) {
        toast(error.message, { error: true });
      }
      await loadPhotos();
    });

    // ---- who you are ------------------------------------------------------

    const aboutCard = section('You', 'Your identity is shown as you write it. What you appear in, and what you see, are separate.');
    const fields = {};

    function selectRow(key, label, values, labels = null) {
      const id = `edit-${key}`;
      const select = createElement('select', { className: 'select', id });
      select.append(createElement('option', { value: '' }, 'Not set'));
      for (const value of values) {
        select.append(createElement('option', { value }, labels ? labels(value) : pretty(value)));
      }
      const wrap = createElement('div', { className: 'field' });
      wrap.append(createElement('label', { for: id }, label), select);
      fields[key] = select;
      return wrap;
    }

    function chipRow(key, label, values, hint) {
      const wrap = createElement('div', { className: 'field' });
      const heading = createElement('span', { className: 'edit__label' }, label);
      const group = createElement('div', { className: 'chips', role: 'group' });
      const chosen = new Set();
      for (const value of values) {
        const chip = createElement(
          'button',
          { type: 'button', className: 'chip', 'aria-pressed': 'false' },
          pretty(value)
        );
        chip.addEventListener('click', () => {
          const on = chosen.has(value);
          if (on) chosen.delete(value);
          else chosen.add(value);
          chip.setAttribute('aria-pressed', String(!on));
        });
        group.append(chip);
      }
      wrap.append(heading, group);
      if (hint) wrap.append(createElement('p', { className: 'hint' }, hint));
      fields[key] = {
        values: () => [...chosen],
        set(list) {
          chosen.clear();
          for (const value of list || []) chosen.add(value);
          [...group.children].forEach((chip, i) => {
            chip.setAttribute('aria-pressed', String(chosen.has(values[i])));
          });
        },
      };
      return wrap;
    }

    const pretty = (v) => String(v).replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());

    // ---- what you wrote ---------------------------------------------------

    const promptCard = section('Your answers', 'Three written answers. This is what people actually read.');
    const promptRows = [];

    // ---- assembly ---------------------------------------------------------

    function section(title, lede) {
      const card = createElement('section', { className: 'edit__card' });
      card.append(createElement('h2', { className: 'edit__title' }, title));
      if (lede) card.append(createElement('p', { className: 'people__muted' }, lede));
      return card;
    }

    function saveButton(card, onSave) {
      const row = createElement('div', { className: 'edit__save' });
      const button = createElement('button', { className: 'btn', type: 'button' }, 'Save');
      const said = createElement('span', { className: 'edit__said' });
      button.addEventListener('click', async () => {
        button.disabled = true;
        said.textContent = '';
        try {
          await onSave();
          said.textContent = 'Saved';
          setTimeout(() => {
            said.textContent = '';
          }, 2500);
        } catch (error) {
          toast(error.message, { error: true });
        }
        button.disabled = false;
      });
      row.append(button, said);
      card.append(row);
    }

    async function load() {
      body.replaceChildren(createElement('p', { className: 'people__muted' }, 'Loading…'));
      try {
        [options, profile] = await Promise.all([api.getOptions(), api.getProfile()]);
      } catch (error) {
        body.replaceChildren(createElement('p', { className: 'people__muted' }, error.message));
        return;
      }

      const F = options.fields || {};

      /* Two groups, not six fields in a column.

         The four selects are short answers about you and pair naturally two
         across; the two chip rows are one decision with two halves — who can
         find you, and who you are shown — and belong together, apart from
         the rest, because they are the only settings here that change what
         the app actually does. Run as one undifferentiated list they read as
         a form to get through rather than as choices to make. */
      const about = createElement('div', { className: 'edit__pair' });
      about.append(
        selectRow(
          'gender_identity_id',
          'Gender identity',
          options.gender_identities.map((g) => g.id),
          (id) => options.gender_identities.find((g) => g.id === id)?.label || id
        ),
        selectRow(
          'sexuality_id',
          'Sexuality',
          options.sexualities.map((s) => s.id),
          (id) => options.sexualities.find((s) => s.id === id)?.label || id
        ),
        selectRow('dating_intentions', 'Dating intentions', F.dating_intentions || []),
        selectRow('relationship_type', 'Relationship type', F.relationship_types || [])
      );

      const reach = createElement('div', { className: 'edit__group' });
      reach.append(
        createElement('h3', { className: 'edit__group-title' }, 'Who sees who'),
        createElement(
          'p',
          { className: 'edit__group-note' },
          'Applied both ways. You only ever appear to people you have also asked to see.'
        ),
        // An array, not two arguments: `createElement(tag, attrs, children)`
        // takes three parameters, so a fourth was dropped on the floor and
        // "I want to see" simply never rendered.
        createElement('div', { className: 'edit__pair' }, [
          chipRow('visible_as', 'Show me in searches for', F.segments || [], 'Who can come across you.'),
          chipRow('interested_in', 'I want to see', F.segments || [], 'Whose profiles you are shown.'),
        ])
      );

      aboutCard.replaceChildren(
        createElement('h2', { className: 'edit__title' }, 'You'),
        createElement(
          'p',
          { className: 'people__muted' },
          'Your identity is shown exactly as you write it.'
        ),
        about,
        reach
      );

      for (const [key, control] of Object.entries(fields)) {
        const value = profile[key];
        if (control.set) control.set(value);
        else control.value = value ?? '';
      }

      saveButton(aboutCard, async () => {
        const patch = {
          gender_identity_id: fields.gender_identity_id.value || null,
          sexuality_id: fields.sexuality_id.value || null,
          dating_intentions: fields.dating_intentions.value || null,
          relationship_type: fields.relationship_type.value || null,
          visible_as: fields.visible_as.values(),
          interested_in: fields.interested_in.values(),
        };
        if (!patch.visible_as.length || !patch.interested_in.length) {
          throw new Error('Pick at least one option in each of the last two questions.');
        }
        profile = await api.patchProfile(patch);
      });

      // --- prompts ---
      promptCard.replaceChildren(
        createElement('h2', { className: 'edit__title' }, 'Your answers'),
        createElement('p', { className: 'people__muted' }, 'This is what people actually read.')
      );
      promptRows.length = 0;
      const written = (profile.prompts || []).filter((p) => p.kind === 'written');
      for (let slot = 1; slot <= 3; slot += 1) {
        const existing = written.find((p) => p.slot === slot);
        const card = createElement('div', { className: 'prompt-slot' });
        card.append(createElement('div', { className: 'prompt-slot__label' }, `Answer ${slot}`));

        const select = createElement('select', { className: 'select', id: `edit-prompt-${slot}` });
        select.append(createElement('option', { value: '' }, 'Choose a prompt…'));
        for (const prompt of options.prompts) {
          select.append(createElement('option', { value: prompt.id }, prompt.text));
        }
        select.value = existing?.prompt_id || '';

        const answer = createElement('textarea', {
          className: 'textarea',
          id: `edit-answer-${slot}`,
          maxlength: '500',
        });
        answer.value = existing?.body || '';

        card.append(select, answer);
        promptCard.append(card);
        promptRows.push({ slot, select, answer });
      }

      saveButton(promptCard, async () => {
        const answers = promptRows
          .filter((r) => r.select.value && r.answer.value.trim())
          .map((r) => ({
            prompt_id: r.select.value,
            slot: r.slot,
            kind: 'written',
            body: r.answer.value.trim(),
          }));
        if (answers.length < 3) {
          throw new Error('All three answers need a prompt and some words.');
        }
        // The voice answer is kept as it was: re-recording belongs with the
        // recorder in onboarding, and silently dropping it here would delete
        // something the person did not ask to delete.
        const voice = (profile.prompts || []).find((p) => p.kind === 'voice');
        if (voice) {
          answers.push({
            prompt_id: voice.prompt_id,
            slot: voice.slot,
            kind: 'voice',
            audio_key: voice.audio_key,
            audio_duration_ms: voice.audio_duration_ms,
          });
        }
        await api.setPrompts(answers);
      });

      body.replaceChildren(photoCard, aboutCard, promptCard);
      await loadPhotos();
    }

    page.mounted = () => {
      nav.mounted();
      load();
    };
    page.destroy = () => nav.destroy();
    return page;
  },
};
