/*
 * Loads a contract's PDF from the app's API. The request carries the same
 * token the app uses (`csa.token` in localStorage), so someone signed in to the
 * app is signed in here too: there is no second sign-in.
 */
async function fetchContractPdf(contractId) {
  let token = null;
  try {
    token = window.localStorage.getItem('csa.token');
  } catch (error) {
    token = null;
  }

  let response;
  try {
    response = await fetch(`/api/contracts/${contractId}/file`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
  } catch (error) {
    throw new Error('Serverga ulanib bo‘lmadi. Internetni tekshirib, sahifani yangilang.');
  }
  if (response.status === 401 || response.status === 403) {
    throw new Error('Sessiya tugagan. Ilovaga qayta kiring, so‘ng bu sahifani yangilang.');
  }
  if (!response.ok) {
    throw new Error(`Shartnoma PDF yuklanmadi (xato ${response.status}).`);
  }
  return response.arrayBuffer();
}
