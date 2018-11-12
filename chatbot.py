#!/usr/bin/env python2
# -*- coding: utf-8 -*-

# LICENSE: GNU Affero General Public License, version 3
# required packages: python-telegram-bot redis

from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
import telegram, logging, time, random, redis, datetime, os, sys
from threading import Timer
import hmac
reload(sys); sys.setdefaultencoding('utf8')

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)

logger = logging.getLogger(__name__)

revoke_timer = None
SG_ID = int(os.environ['SG_ID'])
TOKEN = os.environ['TOKEN']
SECRET = os.environ['SECRET']

challenges = open('challenges').readlines()
names = [i.strip().title() for i in open('names').readlines()]

'''
# Challenges created by
for page in range(1,1000):
    html = fetch_url('https://bbs.pku.edu.cn/v2/thread.php?bid=22&mode=single&page=%d'%page)
    postids = re.findall(r'postid=(\d+)"', html)
    users = re.findall(r'name limit">(\w+)<', html)
    assert len(postids) == len(users)
    for t,u in zip(postids, users): print t,u
'''

class STR: pass
STR.challenge_template = '请输入该帖的作者ID（不区分大小写）：\nhttps://bbs.pku.edu.cn/v2/post-read-single.php?bid=22&postid=%s'
STR.welcome = '请 /verify 认证后获得邀请链接'
STR.already_verified = '已认证，如需重新获取邀请链接 /quit 并重新认证'
STR.too_many = '请明日再试'
STR.succeeded = '''认证成功，欢迎加入。
接下来向本 bot 发送的消息会被匿名转发到群里，使用前请阅读群内置顶消息。

入群链接（1 分钟内有效）：'''
STR.failed = '回答错误，请 /verify 重试'
STR.quitted = '已退出，如需重新认证请 /verify'
STR.forwarded = '消息已转发，如需删除请回复以下命令：\n/delete %s %s'
STR.deleted = '消息已删除'

db = redis.StrictRedis()
today = lambda: datetime.datetime.now().strftime("%F")

def generate_hash(user_id):
    tag = hmac.new(SECRET, str(user_id)).hexdigest()[:4]
    name = names[int(tag, 16)%len(names)]
    return tag, name

def generate_link(bot):
    link = bot.export_chat_invite_link(SG_ID)
    db.set("invite", link, ex=60)
    print link
    return link

def get_invite_link(bot):
    global revoke_timer
    link = db.get("invite") or generate_link(bot)
    if revoke_timer: revoke_timer.cancel()
    revoke_timer = Timer(100, generate_link, args=(bot,))
    revoke_timer.start()
    return link

def get_challenge():
    question, answer = random.choice(challenges).split()
    return STR.challenge_template%question, answer

def auth_rate_limit(user_id):
    key = "count:%s:%s"%(today(), user_id)
    count = db.get(key)
    if count and int(count) > 10:
        return True
    db.incr(key)
    db.expire(key, 86400)
    return False

def ping(bot, update):
    update.message.reply_text('pong')

def start(bot, update):
    update.message.reply_text(STR.welcome)

def quit(bot, update):
    user_id = update.message.from_user.id
    db.set("auth:%s"%user_id, "QUIT")
    update.message.reply_text(STR.quitted)

def delete(bot, update, args):
    try:
        msg_id, tag = args
        if tag != hmac.new(SECRET, str(msg_id)).hexdigest(): raise
        bot.delete_message(chat_id=SG_ID, message_id=int(msg_id))
        update.message.reply_text(STR.deleted)
    except: pass

def verify(bot, update):
    user_id = update.message.from_user.id
    auth = db.get("auth:%s"%user_id)
    if auth == 'OK':
        return update.message.reply_text(STR.already_verified)
    if auth_rate_limit(user_id):
        return update.message.reply_text(STR.too_many)
    question, answer = get_challenge()
    db.set("answer:%s"%user_id, answer, ex=3600)
    db.set("auth:%s"%user_id, "ANSWER")
    update.message.reply_text(question)

def forward_message(bot, msg):
    """Forward a message."""
    if msg.photo:
        r = bot.send_photo(SG_ID, msg.photo[0].file_id, caption=msg.caption)
    elif msg.video:
        r = bot.send_video(SG_ID, msg.video.file_id, caption=msg.caption)
    elif msg.voice:
        r = bot.send_voice(SG_ID, msg.voice.file_id, caption=msg.caption)
    elif msg.document:
        r = bot.send_document(SG_ID, msg.document.file_id, caption=msg.caption)
    elif msg.sticker:
        r = bot.send_sticker(SG_ID, msg.sticker.file_id)
    else:
        if msg.text.startswith(('/anon', '/anno')):
            text = msg.text[5:].strip()
        elif msg.text.startswith('//'):
            text = msg.text[2:].strip()
        else:
            text = '<b>[%s] %s:</b> '%generate_hash(msg.from_user.id) + msg.text_html
        r = bot.send_message(SG_ID, text, parse_mode="HTML")
    return r.message_id

def message(bot, update):
    """Forward the user message, or process verification."""
    msg = update.message
    user_id = msg.from_user.id
    auth = db.get("auth:%s"%user_id)
    if auth == 'OK':
        msg_id = forward_message(bot, msg)
        tag = hmac.new(SECRET, str(msg_id)).hexdigest()
        msg.reply_text(STR.forwarded%(msg_id, tag), reply_to_message_id=msg.message_id)
    elif auth == 'ANSWER':
        answer = db.get("answer:%s"%user_id)
        if answer and msg.text.strip().lower() == answer.lower():
            db.set("auth:%s"%user_id, "OK")
            msg.reply_text(STR.succeeded+get_invite_link(bot))
        else:
            db.set("auth:%s"%user_id, "FAIL")
            msg.reply_text(STR.failed)
    else:
        return start(bot, update)

def error(bot, update, error):
    """Log Errors caused by Updates."""
    logger.warning('Update "%s" caused error "%s"', update, error)

def main():
    """Start the bot."""
    updater = Updater(TOKEN)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("ping", ping))
    dp.add_handler(CommandHandler("start", start, Filters.private))
    dp.add_handler(CommandHandler("verify", verify, Filters.private))
    dp.add_handler(CommandHandler("delete", delete, Filters.private, pass_args=True))
    dp.add_handler(CommandHandler("quit", quit, Filters.private))
    dp.add_handler(MessageHandler(Filters.private, message))

    dp.add_error_handler(error)

    generate_link(updater.bot)

    updater.start_polling()
    updater.idle()

if __name__ == '__main__': main()
