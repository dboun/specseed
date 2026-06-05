export function PostCard({ id, title, state }) {
  return `<article class="post-card"><strong>#${id}</strong> ${title} <span>${state}</span></article>`;
}
