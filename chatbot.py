#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# LICENSE: GNU Affero General Public License, version 3
# required packages: python-telegram-bot

import logging
import os
import random
import time
import threading

from telegram.ext import Updater, CommandHandler, MessageHandler, Filters

logging.basicConfig(format='%(asctime)s - %(name)s - %(message)s')
logger = logging.getLogger(__name__)

now = lambda: time.time()
CH_ID = int(os.environ['CH_ID'])
TOKEN = os.environ['TOKEN']

class G:
    revoke = 1

challenges = open('challenges').readlines()
allowed_status = ("creator", "administrator", "member", "restricted")


class STR: pass


STR.challenge_template = '请输入该帖的作者 ID (不区分大小写):\n' \
                         'https://bbs.pku.edu.cn/v2/post-read-single.php?bid=22&postid=%s'
STR.welcome = '请 /verify 认证后获得邀请链接'
STR.verified = '你已经在频道里，如需帮助请见频道简介里的说明'
STR.too_many = '请 24 小时后再试'
STR.succeeded = '认证成功，欢迎加入\n\n' \
                '邀请链接 (1 分钟内有效):\n%s'
STR.failed = '回答错误，请重试'
STR.reply_dead = '该链接已失效'


class DB:
    def __init__(self):
        self.dict = dict()
        self.ttl = dict()

    def incr(self, key):
        r = int(self.get(key) or 0) + 1
        self.dict[key] = r
        return r

    def pop(self, key):
        self.ttl.pop(key, None)
        return self.dict.pop(key, None)

    def expire(self, key, ex):
        self.ttl[key] = ex + now() if ex else None

    def get(self, key):
        if key not in self.dict:
            return None
        if key in self.ttl and self.ttl[key] < now():
            self.pop(key)
            return None
        return self.dict[key]

    def set(self, key, value, ex=None):
        self.dict[key] = value
        self.expire(key, ex)


db = DB()


def generate_link():
    link = str(bot.export_chat_invite_link(CH_ID))
    db.set("invite", link, ex=60)
    print(link)
    return link


def get_invite_link():
    link = db.get("invite") or generate_link()
    G.revoke = now() + 100
    return link


def get_member_status(user_id):
    try:
        member = bot.get_chat_member(CH_ID, user_id)
        return member.status
    except Exception as e:
        logger.warning('get_member_status error "%s"', e)
        return None


def user_in_group(user_id, use_cache=True):
    k = "auth:%s" % user_id
    if use_cache and db.get(k):
        return db.get(k) > 0
    r = +1 if get_member_status(user_id) in allowed_status else -1
    db.set(k, r, ex=600)
    return r > 0


def get_challenge():
    question, answer = random.choice(challenges).split()
    return STR.challenge_template % question, answer


def auth_rate_limit(user_id, tag, n, exp=72000):
    key = "count:%s:%s" % (tag, user_id)
    count = db.incr(key)
    if count and int(count) > n:
        return True
    db.expire(key, exp)
    return False


def ping(update, context):
    update.message.reply_text('pong')


def start(update, context):
    user_id = update.message.from_user.id
    if not user_in_group(user_id, use_cache=False):
        return update.message.reply_text(STR.welcome)
    update.message.reply_text(STR.verified)


def verify(update, context):
    user_id = update.message.from_user.id
    if user_in_group(user_id, use_cache=False):
        return update.message.reply_text(STR.verified)
    if auth_rate_limit(user_id, 'auth', 10):
        return update.message.reply_text(STR.too_many)
    question, answer = get_challenge()
    db.set("answer:%s" % user_id, answer, ex=3600)
    update.message.reply_text(question)


def message(update, context):
    msg = update.message
    user_id = msg.from_user.id

    if user_in_group(user_id):
        return msg.reply_text(STR.verified)

    answer = db.pop("answer:%s" % user_id)
    if answer is None:
        return start(update, context)

    if msg.text.strip().lower() == answer.lower():
        db.set("auth:%s" % user_id, 1, ex=100)
        msg.reply_text(STR.succeeded % get_invite_link())
    else:
        msg.reply_text(STR.failed)


def error(update, context):
    logger.warning('Update "%s" caused error "%s"', update, context.error)


def timer():
    while 1:
        try:
            t = now()
            if G.revoke and t > G.revoke:
                generate_link()
                G.revoke = None
        except:
            pass
        time.sleep(1)


updater = Updater(TOKEN, use_context=True)
bot = updater.bot
dp = updater.dispatcher

dp.add_handler(CommandHandler("ping", ping))
dp.add_handler(CommandHandler("help", start, Filters.private))
dp.add_handler(CommandHandler("start", start, Filters.private))
dp.add_handler(CommandHandler("verify", verify, Filters.private))
dp.add_handler(MessageHandler(Filters.private, message))

dp.add_error_handler(error)

t = threading.Thread(target=timer)
t.daemon = True
t.start()

updater.start_polling()
updater.idle()
