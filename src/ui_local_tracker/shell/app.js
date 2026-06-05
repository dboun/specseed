import { createPostsFeature } from "../features/posts.js";
import { toast } from "../ui/components.js";

const root = document.querySelector("#app");

const app = createPostsFeature({
  onError(error) {
    toast(error.message || String(error));
  },
});

root.innerHTML = app.html();
app.mount(root);
