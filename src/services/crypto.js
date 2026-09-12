// Web Crypto API E2EE

export async function generateKeyPair() {
  return await window.crypto.subtle.generateKey(
    {
      name: "ECDH",
      namedCurve: "P-256"
    },
    true, // extractable
    ["deriveKey", "deriveBits"]
  );
}

export async function exportPublicKey(publicKey) {
  const jwk = await window.crypto.subtle.exportKey("jwk", publicKey);
  return JSON.stringify(jwk);
}

export async function importPublicKey(jwkString) {
  const jwk = JSON.parse(jwkString);
  return await window.crypto.subtle.importKey(
    "jwk",
    jwk,
    {
      name: "ECDH",
      namedCurve: "P-256"
    },
    true,
    []
  );
}

export async function deriveSharedKey(privateKey, peerPublicKey) {
  return await window.crypto.subtle.deriveKey(
    {
      name: "ECDH",
      public: peerPublicKey
    },
    privateKey,
    {
      name: "AES-GCM",
      length: 256
    },
    false, // non-extractable
    ["encrypt", "decrypt"]
  );
}

export async function encryptMessage(sharedKey, plaintext) {
  const iv = window.crypto.getRandomValues(new Uint8Array(12));
  const encoder = new TextEncoder();
  const encodedText = encoder.encode(plaintext);
  
  const ciphertext = await window.crypto.subtle.encrypt(
    {
      name: "AES-GCM",
      iv: iv
    },
    sharedKey,
    encodedText
  );
  
  return {
    iv: Array.from(iv),
    ciphertext: Array.from(new Uint8Array(ciphertext))
  };
}

export async function decryptMessage(sharedKey, payload) {
  const { iv, ciphertext } = payload;
  const ivArray = new Uint8Array(iv);
  const ctArray = new Uint8Array(ciphertext);
  
  const decryptedText = await window.crypto.subtle.decrypt(
    {
      name: "AES-GCM",
      iv: ivArray
    },
    sharedKey,
    ctArray
  );
  
  const decoder = new TextDecoder();
  return decoder.decode(decryptedText);
}
