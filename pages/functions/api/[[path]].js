// Every /api/* request goes to the lifo-demo Worker unchanged.
export function onRequest(context) {
  return context.env.API.fetch(context.request);
}
