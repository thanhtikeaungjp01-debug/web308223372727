import express, { type Express } from "express";
import cors from "cors";
import type pinoHttpType from "pino-http";
import router from "./routes";
import { logger } from "./lib/logger";

// pino-http is CommonJS; require keeps Vercel's TypeScript checker from
// treating the default import as a namespace object under bundler resolution.
const pinoHttp = require("pino-http") as typeof pinoHttpType;

const app: Express = express();

app.use(
  pinoHttp({
    logger,
    serializers: {
      req(req) {
        return {
          id: req.id,
          method: req.method,
          url: req.url?.split("?")[0],
        };
      },
      res(res) {
        return {
          statusCode: res.statusCode,
        };
      },
    },
  }),
);
app.use(cors());
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

app.use("/api", router);

export default app;
